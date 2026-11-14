package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/hmac"
	crand "crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"net/url"
	"os"
	"os/exec"
	pathpkg "path"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

type idRunner interface {
	ListIDs(context.Context, int64, bool) ([]int64, error)
}

type receivedAtIDRunner interface {
	ListIDsSince(context.Context, int64) ([]int64, error)
}

type watermarkIDRunner interface {
	ListIDsAfter(context.Context, providerCursor) ([]int64, error)
}

type fieldRunner interface {
	ReadField(context.Context, int64, string) ([]byte, error)
}

type simProfileRunner interface {
	ListSIMProfiles(context.Context) ([]simProfile, error)
}

type commandExecutor interface {
	Run(context.Context, string, ...string) ([]byte, error)
}

type execCommandExecutor struct{}

func (execCommandExecutor) Run(ctx context.Context, name string, args ...string) ([]byte, error) {
	return exec.CommandContext(ctx, name, args...).Output()
}

type contentProviderRunner struct {
	executor      commandExecutor
	binary        string
	dumpsysBinary string
	userID        int
}

func (runner contentProviderRunner) ListIDs(ctx context.Context, cursor int64, newOnly bool) ([]int64, error) {
	args := []string{
		"query", "--uri", "content://sms/inbox", "--user", strconv.Itoa(runner.userID),
		"--projection", "_id",
	}
	if newOnly {
		args = append(args, "--where", fmt.Sprintf("_id>%d", cursor))
	}
	args = append(args, "--sort", "date ASC,_id ASC")
	raw, err := runner.executor.Run(ctx, runner.binary, args...)
	if err != nil {
		return nil, err
	}
	return parseIDList(raw, cursor, newOnly)
}

func (runner contentProviderRunner) ListIDsSince(ctx context.Context, receivedAtMillis int64) ([]int64, error) {
	args := []string{
		"query", "--uri", "content://sms/inbox", "--user", strconv.Itoa(runner.userID),
		"--projection", "_id", "--where", fmt.Sprintf("date>=%d", receivedAtMillis),
		"--sort", "date ASC,_id ASC",
	}
	raw, err := runner.executor.Run(ctx, runner.binary, args...)
	if err != nil {
		return nil, err
	}
	return parseIDList(raw, 0, false)
}

func (runner contentProviderRunner) ListIDsAfter(ctx context.Context, cursor providerCursor) ([]int64, error) {
	args := []string{
		"query", "--uri", "content://sms/inbox", "--user", strconv.Itoa(runner.userID),
		"--projection", "_id", "--where", fmt.Sprintf(
			"(date>%d OR (date=%d AND _id>%d))",
			cursor.ReceivedAtMillis,
			cursor.ReceivedAtMillis,
			cursor.RowID,
		),
		"--sort", "date ASC,_id ASC",
	}
	raw, err := runner.executor.Run(ctx, runner.binary, args...)
	if err != nil {
		return nil, err
	}
	return parseIDList(raw, 0, false)
}

func (runner contentProviderRunner) ReadField(ctx context.Context, rowID int64, field string) ([]byte, error) {
	return runner.readContentField(ctx, "content://sms/inbox", rowID, field)
}

func (runner contentProviderRunner) readContentField(ctx context.Context, uri string, rowID int64, field string) ([]byte, error) {
	return runner.executor.Run(
		ctx,
		runner.binary,
		"query", "--uri", uri, "--user", strconv.Itoa(runner.userID),
		"--projection", field, "--where", fmt.Sprintf("_id=%d", rowID),
	)
}

func (runner contentProviderRunner) ListSIMProfiles(ctx context.Context) ([]simProfile, error) {
	profiles, providerErr := runner.listSIMProfilesFromProvider(ctx)
	if runner.dumpsysBinary == "" {
		return profiles, providerErr
	}
	raw, err := runner.executor.Run(ctx, runner.dumpsysBinary, "isub")
	if err != nil {
		if len(profiles) > 0 {
			return profiles, nil
		}
		if providerErr != nil {
			return nil, providerErr
		}
		return nil, err
	}
	dumpsysProfiles := parseDumpsysSubscriptionProfiles(raw)
	if len(profiles) > 0 {
		return mergeSIMProfiles(profiles, dumpsysProfiles), nil
	}
	if len(dumpsysProfiles) > 0 {
		return dumpsysProfiles, nil
	}
	return profiles, providerErr
}

func mergeSIMProfiles(providerProfiles, dumpsysProfiles []simProfile) []simProfile {
	merged := append([]simProfile(nil), providerProfiles...)
	for index := range merged {
		if merged[index].SubscriptionID == nil {
			continue
		}
		for _, supplement := range dumpsysProfiles {
			if supplement.SubscriptionID == nil || *supplement.SubscriptionID != *merged[index].SubscriptionID {
				continue
			}
			if supplement.SlotIndex != nil {
				merged[index].SlotIndex = supplement.SlotIndex
			}
			if merged[index].PhoneNumber == "" {
				merged[index].PhoneNumber = supplement.PhoneNumber
			}
			if merged[index].CarrierName == "" {
				merged[index].CarrierName = supplement.CarrierName
			}
			break
		}
	}
	return merged
}

func (runner contentProviderRunner) listSIMProfilesFromProvider(ctx context.Context) ([]simProfile, error) {
	raw, err := runner.executor.Run(
		ctx,
		runner.binary,
		"query", "--uri", "content://telephony/siminfo", "--user", strconv.Itoa(runner.userID),
		"--projection", "_id", "--sort", "_id ASC",
	)
	if err != nil {
		return nil, err
	}
	ids, err := parseIDList(raw, 0, false)
	if err != nil {
		return nil, err
	}
	profiles := make([]simProfile, 0, len(ids))
	for _, id := range ids {
		profile := simProfile{}
		if value, ok := runner.readSIMField(ctx, id, "slot_index"); ok {
			profile.SlotIndex = parseNonNegativeInt(value)
		}
		if value, ok := runner.readSIMField(ctx, id, "sim_id"); ok {
			profile.SIMID = parseNonNegativeInt(value)
			if profile.SlotIndex == nil && profile.SIMID != nil && *profile.SIMID <= 1 {
				slot := *profile.SIMID
				profile.SlotIndex = &slot
			}
		}
		if value, ok := runner.readSIMField(ctx, id, "subscription_id"); ok {
			profile.SubscriptionID = parseNonNegativeInt(value)
		}
		if profile.SubscriptionID == nil {
			if value, ok := runner.readSIMField(ctx, id, "_id"); ok {
				profile.SubscriptionID = parseNonNegativeInt(value)
			}
		}
		if value, ok := runner.readSIMField(ctx, id, "number"); ok {
			profile.PhoneNumber = value
		}
		if profile.PhoneNumber == "" {
			if value, ok := runner.readSIMField(ctx, id, "phone_number"); ok {
				profile.PhoneNumber = value
			}
		}
		if profile.PhoneNumber == "" {
			for _, field := range []string{"phone_number_source_ims", "phone_number_source_carrier"} {
				if value, ok := runner.readSIMField(ctx, id, field); ok && value != "" {
					profile.PhoneNumber = value
					break
				}
			}
		}
		if value, ok := runner.readSIMField(ctx, id, "carrier_name"); ok {
			profile.CarrierName = value
		}
		profiles = append(profiles, profile)
	}
	return profiles, nil
}

func parseDumpsysSubscriptionProfiles(raw []byte) []simProfile {
	profiles := make([]simProfile, 0)
	seen := make(map[int]bool)
	for _, block := range strings.Split(string(raw), "\n") {
		if !strings.Contains(block, "simSlotIndex=") {
			continue
		}
		fields := make(map[string]string)
		for _, name := range []string{"id", "simSlotIndex", "mNumber", "number", "carrierName"} {
			if value, ok := parseDumpsysField(block, name); ok {
				fields[name] = value
			}
		}
		subscriptionID, err := strconv.Atoi(fields["id"])
		if err != nil || subscriptionID < 0 || seen[subscriptionID] {
			continue
		}
		slotIndex, err := strconv.Atoi(fields["simSlotIndex"])
		if err != nil || slotIndex < 0 {
			continue
		}
		seen[subscriptionID] = true
		slot := slotIndex
		profile := simProfile{
			SlotIndex:      &slot,
			SubscriptionID: &subscriptionID,
			PhoneNumber:    dumpsysOptionalValue(firstNonEmpty(fields["mNumber"], fields["number"])),
			CarrierName:    dumpsysOptionalValue(fields["carrierName"]),
		}
		profiles = append(profiles, profile)
	}
	sort.SliceStable(profiles, func(i, j int) bool {
		return *profiles[i].SlotIndex < *profiles[j].SlotIndex
	})
	return profiles
}

func parseDumpsysField(block, name string) (string, bool) {
	marker := name + "="
	start := strings.Index(block, marker)
	if start < 0 {
		return "", false
	}
	value := block[start+len(marker):]
	if end := strings.IndexAny(value, " ,}]"); end >= 0 {
		value = value[:end]
	}
	return value, value != ""
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func dumpsysOptionalValue(value string) string {
	if value == "" || value == "null" || strings.Contains(value, "****") {
		return ""
	}
	return value
}

func (runner contentProviderRunner) readSIMField(ctx context.Context, rowID int64, field string) (string, bool) {
	raw, err := runner.readContentField(ctx, "content://telephony/siminfo", rowID, field)
	if err != nil {
		return "", false
	}
	value, err := parseSingleField(raw, field)
	if err != nil {
		return "", false
	}
	return value, true
}

func parseNonNegativeInt(value string) *int {
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < 0 {
		return nil
	}
	return &parsed
}

func parseIDList(raw []byte, cursor int64, newOnly bool) ([]int64, error) {
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" || trimmed == "No result found." {
		return nil, nil
	}
	var ids []int64
	scanner := bufio.NewScanner(bytes.NewReader(raw))
	for scanner.Scan() {
		line := strings.TrimSuffix(scanner.Text(), "\r")
		marker := strings.Index(line, " _id=")
		if !strings.HasPrefix(line, "Row: ") || marker < 0 {
			return nil, fmt.Errorf("unexpected content ID output")
		}
		id, err := strconv.ParseInt(line[marker+len(" _id="):], 10, 64)
		if err != nil || id < 0 {
			return nil, fmt.Errorf("invalid content ID")
		}
		if !newOnly || id > cursor {
			ids = append(ids, id)
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids, nil
}

type providerReader struct {
	runner            idRunner
	fields            fieldRunner
	simProfiles       simProfileRunner
	cursorPath        string
	now               func() time.Time
	lastScanFound     bool
	simProfilesLoaded bool
	simProfilesCache  []simProfile
	simProfilesErr    error
}

type providerCursor struct {
	ReceivedAtMillis int64 `json:"received_at_millis"`
	RowID            int64 `json:"row_id"`
}

var errLegacyProviderCursor = errors.New("legacy provider cursor")

type smsEvent struct {
	SchemaVersion   int           `json:"schema_version"`
	ClientMessageID string        `json:"client_message_id"`
	ReceivedAt      string        `json:"received_at"`
	Sender          string        `json:"sender"`
	Body            string        `json:"body"`
	Source          string        `json:"source"`
	SIMSlot         *int          `json:"sim_slot,omitempty"`
	Metadata        eventMetadata `json:"metadata"`
}

type eventMetadata struct {
	SourceMessageID string `json:"source_message_id"`
	SubscriptionID  *int   `json:"subscription_id,omitempty"`
	SIMID           *int   `json:"sim_id,omitempty"`
	CarrierName     string `json:"carrier_name,omitempty"`
	PhoneNumber     string `json:"phone_number,omitempty"`
}

type rootConfig struct {
	SchemaVersion          int    `json:"schema_version"`
	Enabled                bool   `json:"enabled"`
	BaseURL                string `json:"base_url"`
	AllowInsecureLocalHTTP bool   `json:"allow_insecure_local_http"`
	TransportMode          string `json:"transport_mode"`
	FallbackToHTTP         bool   `json:"fallback_to_http"`
	MQTTBrokerURL          string `json:"mqtt_broker_url"`
	MQTTTopic              string `json:"mqtt_topic"`
	MQTTUsername           string `json:"mqtt_username"`
	MQTTPassword           string `json:"mqtt_password"`
	DeviceID               string `json:"device_id"`
	HMACSecret             string `json:"hmac_secret"`
	Source                 string `json:"source"`
	AndroidUserID          int    `json:"android_user_id"`
	InitialSyncMode        string `json:"initial_sync_mode"`
	PollIntervalSeconds    int    `json:"poll_interval_seconds"`
	MaxBackoffSeconds      int    `json:"max_backoff_seconds"`
	WatcherIdleSeconds     int    `json:"watcher_idle_seconds"`
	SMSDatabasePath        string `json:"sms_database_path"`
	AllowQueryBattery      bool   `json:"allow_query_battery"`
	AllowQueryRecentSMS    bool   `json:"allow_query_recent_sms"`
	AllowTriggerUpload     bool   `json:"allow_trigger_upload"`
}

type readOptions struct {
	stateDir     string
	spoolDir     string
	userID       int
	configPath   string
	databasePath string
}

func parseSingleField(raw []byte, field string) (string, error) {
	value := raw
	if bytes.HasSuffix(value, []byte("\n")) {
		value = value[:len(value)-1]
		if bytes.HasSuffix(value, []byte("\r")) {
			value = value[:len(value)-1]
		}
	}

	prefix := []byte("Row: 0 " + field + "=")
	if !bytes.HasPrefix(value, prefix) {
		return "", fmt.Errorf("unexpected content output for %s", field)
	}
	return string(value[len(prefix):]), nil
}

func eventID(source string, rowID int64, receivedAtMillis int64) string {
	return fmt.Sprintf("root:%s:%d:%d", source, rowID, receivedAtMillis)
}

func decodeSecret(encoded string) ([]byte, error) {
	secret, err := base64.RawURLEncoding.DecodeString(encoded)
	if err != nil || len(secret) != 32 {
		return nil, fmt.Errorf("secret must be unpadded base64url encoding of 32 bytes")
	}
	allSame := true
	for _, current := range secret[1:] {
		if current != secret[0] {
			allSame = false
			break
		}
	}
	if allSame {
		return nil, fmt.Errorf("secret is not a production credential")
	}
	if utf8.Valid(secret) {
		text := strings.ToLower(string(secret))
		for _, sentinel := range []string{
			"placeholder", "change_me", "changeme", "replace_me", "replace-me",
			"your_secret", "your-secret", "not_enrolled", "todo", "xxx",
		} {
			if strings.Contains(text, sentinel) {
				return nil, fmt.Errorf("secret is not a production credential")
			}
		}
	}
	return secret, nil
}

func sign(secret []byte, timestamp string, nonce string, body []byte) string {
	bodyHash := sha256.Sum256(body)
	return signHash(secret, timestamp, nonce, fmt.Sprintf("%x", bodyHash))
}

func signHash(secret []byte, timestamp, nonce, bodyHash string) string {
	canonical := fmt.Sprintf("%s\n%s\n%s", timestamp, nonce, bodyHash)
	mac := hmac.New(sha256.New, secret)
	_, _ = mac.Write([]byte(canonical))
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func hashFile(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hasher := sha256.New()
	if _, err := io.Copy(hasher, file); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", hasher.Sum(nil)), nil
}

func hmacFile(secret []byte, timestamp, nonce, path string) (string, error) {
	bodyHash, err := hashFile(path)
	if err != nil {
		return "", err
	}
	return signHash(secret, timestamp, nonce, bodyHash), nil
}

func newNonce(source io.Reader) (string, error) {
	raw := make([]byte, 24)
	if _, err := io.ReadFull(source, raw); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(raw), nil
}

func marshalEvent(event smsEvent) ([]byte, error) {
	return json.Marshal(event)
}

func spoolFileName(eventID string) string {
	if runtime.GOOS == "windows" {
		return strings.ReplaceAll(eventID, ":", "_") + ".json"
	}
	return eventID + ".json"
}

func loadConfig(path string) (rootConfig, error) {
	file, err := os.Open(path)
	if err != nil {
		return rootConfig{}, err
	}
	defer file.Close()
	raw, err := io.ReadAll(io.LimitReader(file, 16*1024+1))
	if err != nil {
		return rootConfig{}, err
	}
	if len(raw) > 16*1024 {
		return rootConfig{}, fmt.Errorf("config too large")
	}
	if err := rejectDuplicateTopLevelKeys(raw); err != nil {
		return rootConfig{}, err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var config rootConfig
	if err := decoder.Decode(&config); err != nil {
		return rootConfig{}, err
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return rootConfig{}, fmt.Errorf("config must contain one JSON object")
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return rootConfig{}, err
	}
	if config.TransportMode == "" {
		config.TransportMode = "http"
	}
	if _, exists := fields["fallback_to_http"]; !exists {
		config.FallbackToHTTP = true
	}
	if err := validateConfig(config); err != nil {
		return rootConfig{}, err
	}
	return config, nil
}

func rejectDuplicateTopLevelKeys(raw []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delimiter, ok := token.(json.Delim)
	if !ok || delimiter != '{' {
		return fmt.Errorf("config root must be an object")
	}
	seen := make(map[string]bool)
	for decoder.More() {
		keyToken, err := decoder.Token()
		if err != nil {
			return err
		}
		key, ok := keyToken.(string)
		if !ok || seen[key] {
			return fmt.Errorf("duplicate config field")
		}
		seen[key] = true
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			return err
		}
	}
	if _, err := decoder.Token(); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return fmt.Errorf("config must contain one JSON object")
	}
	return nil
}

func normalizeBaseURL(raw string, allowInsecureLocalHTTP bool) (string, error) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return "", fmt.Errorf("missing base URL")
	}
	if !strings.Contains(value, "://") {
		if !allowInsecureLocalHTTP {
			return "", fmt.Errorf("base URL scheme is required")
		}
		value = "http://" + value
	}
	return value, nil
}

func validateConfig(config rootConfig) error {
	if config.SchemaVersion != 2 || config.Source != "root_module" {
		return fmt.Errorf("unsupported config")
	}
	if config.AndroidUserID < 0 || config.InitialSyncMode != "new_only" {
		return fmt.Errorf("unsupported reader config")
	}
	if config.TransportMode == "" {
		config.TransportMode = "http"
	}
	if config.DeviceID != "" && !validDeviceID(config.DeviceID) {
		return fmt.Errorf("invalid device ID")
	}
	if config.Enabled && config.DeviceID == "" {
		return fmt.Errorf("missing device ID")
	}
	if config.TransportMode != "http" && config.TransportMode != "ws" && config.TransportMode != "mqtt" {
		return fmt.Errorf("unsupported transport mode")
	}
	if config.TransportMode == "mqtt" {
		if err := validateMQTTBrokerURL(config.MQTTBrokerURL); err != nil {
			return err
		}
		if !validConfiguredMQTTTopic(config.MQTTTopic, config.DeviceID) {
			return fmt.Errorf("invalid MQTT topic")
		}
	}
	if err := validateManualSQLitePath(config.SMSDatabasePath); err != nil {
		return err
	}
	if config.PollIntervalSeconds < 1 || config.PollIntervalSeconds > 3600 ||
		config.MaxBackoffSeconds < 1 || config.MaxBackoffSeconds > 3600 ||
		(config.WatcherIdleSeconds != 0 && (config.WatcherIdleSeconds < 1 || config.WatcherIdleSeconds > 3600)) {
		return fmt.Errorf("invalid timing config")
	}
	parsed, err := url.Parse(config.BaseURL)
	if err != nil || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return fmt.Errorf("invalid base URL")
	}
	if parsed.Port() != "" {
		port, portErr := strconv.Atoi(parsed.Port())
		if portErr != nil || port < 1 || port > 65535 {
			return fmt.Errorf("invalid base URL port")
		}
	}
	switch parsed.Scheme {
	case "https":
		// Production endpoints may use a DNS name or a literal IP address.
	case "http":
		if !config.AllowInsecureLocalHTTP {
			return fmt.Errorf("insecure HTTP endpoint requires allow_insecure_local_http")
		}
		if parsed.Port() == "" {
			return fmt.Errorf("insecure HTTP endpoint requires an explicit port")
		}
		address, addressErr := netip.ParseAddr(parsed.Hostname())
		if addressErr != nil || (!address.IsPrivate() && !address.IsLoopback()) {
			return fmt.Errorf("insecure HTTP endpoint must use a private or loopback IP")
		}
	default:
		return fmt.Errorf("invalid base URL")
	}
	if config.Enabled {
		if _, err := decodeSecret(config.HMACSecret); err != nil {
			return err
		}
	}
	return nil
}

func validDeviceID(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && strings.EqualFold(parsed.String(), value)
}

func validateManualSQLitePath(value string) error {
	if value == "" {
		return nil
	}
	if !strings.HasPrefix(value, "/") || strings.Contains(value, "\\") || pathpkg.Clean(value) != value || pathpkg.Base(value) != "mmssms.db" {
		return fmt.Errorf("invalid SMS database path")
	}
	if strings.HasPrefix(value, "/data/data/com.android.providers.telephony/databases/") {
		return nil
	}
	for _, prefix := range []string{"/data/user/", "/data/user_de/"} {
		if !strings.HasPrefix(value, prefix) {
			continue
		}
		remainder := strings.TrimPrefix(value, prefix)
		separator := strings.IndexByte(remainder, '/')
		if separator < 1 {
			break
		}
		parsedUserID, err := strconv.Atoi(remainder[:separator])
		if err != nil || parsedUserID < 0 {
			break
		}
		if remainder[separator:] == "/com.android.providers.telephony/databases/mmssms.db" {
			return nil
		}
	}
	return fmt.Errorf("SMS database path is outside TelephonyProvider storage")
}

func parseReadOptions(args []string) (readOptions, error) {
	if len(args) != 8 {
		return readOptions{}, fmt.Errorf("invalid read arguments")
	}
	values := make(map[string]string, 4)
	for index := 0; index < len(args); index += 2 {
		name := args[index]
		if name != "--state-dir" && name != "--spool-dir" && name != "--user-id" && name != "--config" {
			return readOptions{}, fmt.Errorf("unknown read argument")
		}
		if _, exists := values[name]; exists {
			return readOptions{}, fmt.Errorf("duplicate read argument")
		}
		values[name] = args[index+1]
	}
	stateDir := values["--state-dir"]
	spoolDir := values["--spool-dir"]
	configPath := values["--config"]
	if !strings.HasPrefix(stateDir, "/") || !strings.HasPrefix(spoolDir, "/") || !strings.HasPrefix(configPath, "/") {
		return readOptions{}, fmt.Errorf("read directories must be absolute")
	}
	userID, err := strconv.Atoi(values["--user-id"])
	if err != nil || userID < 0 {
		return readOptions{}, fmt.Errorf("invalid Android user ID")
	}
	return readOptions{stateDir: stateDir, spoolDir: spoolDir, userID: userID, configPath: configPath}, nil
}

func runProviderRead(ctx context.Context, options readOptions) error {
	config, err := loadConfig(options.configPath)
	if err != nil || !config.Enabled {
		return fmt.Errorf("invalid reader config")
	}
	if err := os.MkdirAll(options.stateDir, 0o700); err != nil {
		return err
	}
	if err := os.Chmod(options.stateDir, 0o700); err != nil {
		return err
	}
	if err := os.MkdirAll(options.spoolDir, 0o700); err != nil {
		return err
	}
	if err := os.Chmod(options.spoolDir, 0o700); err != nil {
		return err
	}
	runner := contentProviderRunner{
		executor:      execCommandExecutor{},
		binary:        "/system/bin/content",
		dumpsysBinary: "/system/bin/dumpsys",
		userID:        options.userID,
	}
	reader := providerReader{
		runner:      runner,
		fields:      runner,
		simProfiles: runner,
		cursorPath:  filepath.Join(options.stateDir, "provider.cursor"),
	}
	baseline, err := readProviderCursor(reader.cursorPath)
	if err != nil {
		if !os.IsNotExist(err) && !errors.Is(err, errLegacyProviderCursor) {
			return err
		}
		baseline = providerCursor{ReceivedAtMillis: time.Now().UnixMilli()}
		if err := writeProviderCursor(reader.cursorPath, baseline); err != nil {
			return err
		}
	}
	options.databasePath = config.SMSDatabasePath
	writeReaderDiagnostic(options.stateDir, readerDiagnostic{Mode: "自动选择", Source: "自动"})
	if options.databasePath != "" {
		if _, err := validateSQLiteDatabase(ctx, options.databasePath); err != nil {
			writeReaderDiagnostic(options.stateDir, readerDiagnostic{Mode: "手动数据库", Path: options.databasePath, Source: "手动", ErrorCode: databaseErrorCode(err)})
			return err
		}
	}
	sqliteErr := runSQLiteFallback(ctx, options, baseline)
	if sqliteErr == nil {
		return nil
	}
	providerErr := reader.scan(ctx, options.spoolDir, "new_only")
	if providerErr == nil {
		writeReaderDiagnostic(options.stateDir, readerDiagnostic{Mode: "Content Provider", Source: databaseSource(options.databasePath), ErrorCode: ""})
		return nil
	}
	var partialSQLiteErr *sqliteFallbackError
	if errors.As(sqliteErr, &partialSQLiteErr) && partialSQLiteErr.completed {
		return nil
	}
	return providerErr
}

func databaseSource(path string) string {
	if path == "" {
		return "自动"
	}
	return "手动"
}

func runProviderThenFallback(
	ctx context.Context,
	provider func(context.Context) (bool, error),
	fallback func(context.Context) error,
) error {
	found, providerErr := provider(ctx)
	fallbackErr := fallback(ctx)
	if providerErr == nil && fallbackErr == nil {
		return nil
	}
	if providerErr == nil && found {
		// Provider delivered at least one row. SQLite is an optional supplement
		// for ROM-filtered rows, so its absence must not stop normal uploads.
		return nil
	}
	if providerErr == nil {
		return fallbackErr
	}
	if fallbackErr == nil {
		return nil
	}
	return fmt.Errorf("provider read failed: %v; database fallback failed: %v", providerErr, fallbackErr)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage_invalid")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "check-config":
		if len(os.Args) != 4 || os.Args[2] != "--config" {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		if _, err := loadConfig(os.Args[3]); err != nil {
			fmt.Fprintln(os.Stderr, "config_invalid")
			os.Exit(1)
		}
		fmt.Println("ready")
	case "read":
		options, err := parseReadOptions(os.Args[2:])
		if err != nil {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		if err := runProviderRead(context.Background(), options); err != nil {
			fmt.Fprintln(os.Stderr, "reader_failed")
			os.Exit(1)
		}
		fmt.Println("scan_complete")
	case "web":
		options, err := parseWebOptions(os.Args[2:])
		if err != nil {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		if err := runWebConsole(options); err != nil {
			fmt.Fprintln(os.Stderr, "web_unavailable")
			os.Exit(1)
		}
	case "web-token":
		if len(os.Args) != 4 || os.Args[2] != "--state-dir" {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		token, err := readWebToken(os.Args[3])
		if err != nil {
			fmt.Fprintln(os.Stderr, "web_unavailable")
			os.Exit(1)
		}
		fmt.Println(token)
	case "--self-test":
		if len(os.Args) != 2 {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		fmt.Println("ready")
	case "nonce":
		if len(os.Args) != 2 {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		nonce, err := newNonce(crand.Reader)
		if err != nil {
			fmt.Fprintln(os.Stderr, "nonce_unavailable")
			os.Exit(1)
		}
		fmt.Println(nonce)
	case "sha256":
		if len(os.Args) != 4 || os.Args[2] != "--body" {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		hash, err := hashFile(os.Args[3])
		if err != nil {
			fmt.Fprintln(os.Stderr, "hash_failed")
			os.Exit(1)
		}
		fmt.Println(hash)
	case "hmac":
		if len(os.Args) != 10 || os.Args[2] != "--config" || os.Args[4] != "--timestamp" ||
			os.Args[6] != "--nonce" || os.Args[8] != "--body" {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		config, err := loadConfig(os.Args[3])
		if err != nil || !config.Enabled {
			fmt.Fprintln(os.Stderr, "config_invalid")
			os.Exit(1)
		}
		secret, err := decodeSecret(config.HMACSecret)
		if err != nil {
			fmt.Fprintln(os.Stderr, "credential_invalid")
			os.Exit(1)
		}
		signature, err := hmacFile(secret, os.Args[5], os.Args[7], os.Args[9])
		if err != nil {
			fmt.Fprintln(os.Stderr, "signature_failed")
			os.Exit(1)
		}
		fmt.Println(signature)
	case "commands":
		if len(os.Args) != 4 || os.Args[2] != "--config" {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		if err := runDeviceCommands(context.Background(), os.Args[3]); err != nil {
			fmt.Fprintln(os.Stderr, "command_poll_failed")
			os.Exit(1)
		}
		fmt.Println("commands_complete")
	case "transport-upload":
		options, err := parseTransportUploadOptions(os.Args[2:])
		if err != nil {
			fmt.Fprintln(os.Stderr, "usage_invalid")
			os.Exit(2)
		}
		status, err := uploadTransport(context.Background(), options.configPath, options.bodyPath, options.timestamp, options.nonce, options.idempotencyKey, options.mode)
		if err != nil {
			fmt.Fprintln(os.Stderr, "transport_failed")
			os.Exit(1)
		}
		fmt.Println(status)
	default:
		fmt.Fprintln(os.Stderr, "usage_invalid")
		os.Exit(2)
	}
}

func (reader *providerReader) scan(ctx context.Context, spool, initialSyncMode string) error {
	reader.lastScanFound = false
	if initialSyncMode != "new_only" {
		return fmt.Errorf("unsupported initial sync mode")
	}
	receivedRunner, receivedOK := reader.runner.(receivedAtIDRunner)
	watermarkRunner, watermarkOK := reader.runner.(watermarkIDRunner)
	if !receivedOK && !watermarkOK {
		return fmt.Errorf("provider does not support date cursor")
	}
	cursor, err := readProviderCursor(reader.cursorPath)
	if err != nil {
		if !os.IsNotExist(err) && !errors.Is(err, errLegacyProviderCursor) {
			return err
		}
		clock := reader.now
		if clock == nil {
			clock = time.Now
		}
		ids, listErr := reader.runner.ListIDs(ctx, 0, false)
		if listErr != nil {
			return listErr
		}
		latest := int64(0)
		for _, id := range ids {
			if id > latest {
				latest = id
			}
		}
		return writeProviderCursor(reader.cursorPath, providerCursor{
			ReceivedAtMillis: clock().UnixMilli(),
			RowID:            latest,
		})
	}
	if reader.fields == nil {
		return fmt.Errorf("provider field reader unavailable")
	}
	var ids []int64
	if watermarkOK {
		ids, err = watermarkRunner.ListIDsAfter(ctx, cursor)
	} else {
		ids, err = receivedRunner.ListIDsSince(ctx, cursor.ReceivedAtMillis)
	}
	if err != nil {
		return err
	}
	reader.lastScanFound = len(ids) > 0
	type eventWithID struct {
		id    int64
		event smsEvent
		when  int64
	}
	events := make([]eventWithID, 0, len(ids))
	for _, id := range ids {
		event, readErr := reader.readEvent(ctx, id)
		if readErr != nil {
			return readErr
		}
		when, parseErr := time.Parse(time.RFC3339Nano, event.ReceivedAt)
		if parseErr != nil {
			return parseErr
		}
		events = append(events, eventWithID{id: id, event: event, when: when.UnixMilli()})
	}
	sort.SliceStable(events, func(i, j int) bool {
		if events[i].when != events[j].when {
			return events[i].when < events[j].when
		}
		return events[i].id < events[j].id
	})
	for _, item := range events {
		if item.when < cursor.ReceivedAtMillis ||
			(item.when == cursor.ReceivedAtMillis && item.id <= cursor.RowID) {
			continue
		}
		if err := writeSpoolEvent(spool, item.event); err != nil {
			return err
		}
		cursor = providerCursor{ReceivedAtMillis: item.when, RowID: item.id}
		if err := writeProviderCursor(reader.cursorPath, cursor); err != nil {
			return err
		}
	}
	return nil
}

func (reader *providerReader) readEvent(ctx context.Context, id int64) (smsEvent, error) {
	if reader.fields == nil {
		return smsEvent{}, fmt.Errorf("provider field reader unavailable")
	}
	readRequired := func(field string) (string, error) {
		raw, err := reader.fields.ReadField(ctx, id, field)
		if err != nil {
			return "", err
		}
		return parseSingleField(raw, field)
	}
	rowIDText, err := readRequired("_id")
	if err != nil {
		return smsEvent{}, err
	}
	rowID, err := strconv.ParseInt(rowIDText, 10, 64)
	if err != nil || rowID != id {
		return smsEvent{}, fmt.Errorf("invalid provider row id")
	}
	sender, err := readRequired("address")
	if err != nil {
		return smsEvent{}, err
	}
	body, err := readRequired("body")
	if err != nil {
		return smsEvent{}, err
	}
	dateText, err := readRequired("date")
	if err != nil {
		return smsEvent{}, err
	}
	dateMillis, err := strconv.ParseInt(dateText, 10, 64)
	if err != nil || dateMillis < 0 {
		return smsEvent{}, fmt.Errorf("invalid provider date")
	}
	event := smsEvent{
		SchemaVersion:   1,
		ClientMessageID: eventID("provider", rowID, dateMillis),
		ReceivedAt:      time.UnixMilli(dateMillis).UTC().Format(time.RFC3339Nano),
		Sender:          sender,
		Body:            body,
		Source:          "root_module",
		Metadata: eventMetadata{
			SourceMessageID: strconv.FormatInt(rowID, 10),
		},
	}
	if value, ok := reader.readOptionalField(ctx, id, "slot_index"); ok {
		if slot, err := strconv.Atoi(value); err == nil && slot >= 0 {
			event.SIMSlot = &slot
		}
	}
	if value, ok := reader.readOptionalField(ctx, id, "sub_id"); ok {
		if subscriptionID, err := strconv.Atoi(value); err == nil && subscriptionID >= 0 {
			event.Metadata.SubscriptionID = &subscriptionID
		}
	}
	if event.Metadata.SubscriptionID == nil {
		if value, ok := reader.readOptionalField(ctx, id, "subscription_id"); ok {
			if subscriptionID, err := strconv.Atoi(value); err == nil && subscriptionID >= 0 {
				event.Metadata.SubscriptionID = &subscriptionID
			}
		}
	}
	if value, ok := reader.readOptionalField(ctx, id, "sim_id"); ok {
		event.Metadata.SIMID = parseNonNegativeInt(value)
	}
	if reader.simProfiles != nil &&
		(event.Metadata.SubscriptionID != nil || event.Metadata.SIMID != nil || event.SIMSlot != nil) {
		if profiles, err := reader.loadSIMProfiles(ctx); err == nil {
			if snapshot, ok := chooseSIMSnapshot(
				profiles,
				event.Metadata.SubscriptionID,
				event.Metadata.SIMID,
				event.SIMSlot,
			); ok {
				if snapshot.SlotIndex != nil {
					event.SIMSlot = snapshot.SlotIndex
				}
				if snapshot.SubscriptionID != nil {
					event.Metadata.SubscriptionID = snapshot.SubscriptionID
				}
				if snapshot.SIMID != nil {
					event.Metadata.SIMID = snapshot.SIMID
				}
				event.Metadata.PhoneNumber = snapshot.PhoneNumber
				event.Metadata.CarrierName = snapshot.CarrierName
			}
		}
	}
	return event, nil
}

func (reader *providerReader) loadSIMProfiles(ctx context.Context) ([]simProfile, error) {
	if !reader.simProfilesLoaded {
		reader.simProfilesCache, reader.simProfilesErr = reader.simProfiles.ListSIMProfiles(ctx)
		reader.simProfilesLoaded = true
	}
	return reader.simProfilesCache, reader.simProfilesErr
}

func (reader providerReader) readOptionalField(ctx context.Context, id int64, field string) (string, bool) {
	raw, err := reader.fields.ReadField(ctx, id, field)
	if err != nil {
		return "", false
	}
	value, err := parseSingleField(raw, field)
	if err != nil {
		return "", false
	}
	return value, true
}

func writeSpoolEvent(spoolDir string, event smsEvent) error {
	if err := os.MkdirAll(spoolDir, 0o700); err != nil {
		return err
	}
	raw, err := marshalEvent(event)
	if err != nil {
		return err
	}
	temporary, err := os.CreateTemp(spoolDir, ".root-provider-*.tmp")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	removeTemporary := true
	defer func() {
		_ = temporary.Close()
		if removeTemporary {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return err
	}
	if _, err := temporary.Write(raw); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	target := filepath.Join(spoolDir, spoolFileName(event.ClientMessageID))
	if _, err := os.Stat(target); err == nil {
		return nil
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := os.Rename(temporaryPath, target); err != nil {
		if _, statErr := os.Stat(target); statErr == nil {
			removeTemporary = false
			return nil
		}
		return err
	}
	removeTemporary = false
	return nil
}

func readProviderCursor(path string) (providerCursor, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return providerCursor{}, err
	}
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 {
		return providerCursor{}, fmt.Errorf("empty provider cursor")
	}
	if _, parseErr := strconv.ParseInt(string(trimmed), 10, 64); parseErr == nil {
		return providerCursor{}, errLegacyProviderCursor
	}
	decoder := json.NewDecoder(bytes.NewReader(trimmed))
	decoder.DisallowUnknownFields()
	var cursor providerCursor
	if err := decoder.Decode(&cursor); err != nil {
		return providerCursor{}, err
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return providerCursor{}, fmt.Errorf("provider cursor must contain one object")
	}
	if cursor.ReceivedAtMillis < 0 || cursor.RowID < 0 {
		return providerCursor{}, fmt.Errorf("invalid provider cursor")
	}
	return cursor, nil
}

func writeProviderCursor(path string, cursor providerCursor) error {
	if cursor.ReceivedAtMillis < 0 || cursor.RowID < 0 {
		return fmt.Errorf("invalid provider cursor")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	raw, err := json.Marshal(cursor)
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	temporary, err := os.CreateTemp(filepath.Dir(path), ".provider.cursor.*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	removeTemporary := true
	defer func() {
		_ = temporary.Close()
		if removeTemporary {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return err
	}
	if _, err := temporary.Write(raw); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return err
	}
	removeTemporary = false
	return nil
}

func readCursor(path string) (int64, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	value := strings.TrimSpace(string(raw))
	if value == "" {
		return 0, fmt.Errorf("empty provider cursor")
	}
	cursor, err := strconv.ParseInt(value, 10, 64)
	if err != nil || cursor < 0 {
		return 0, fmt.Errorf("invalid provider cursor")
	}
	return cursor, nil
}

func writeCursor(path string, cursor int64) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".provider.cursor.*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	removeTemporary := true
	defer func() {
		_ = temporary.Close()
		if removeTemporary {
			_ = os.Remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return err
	}
	if _, err := fmt.Fprintf(temporary, "%d\n", cursor); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return err
	}
	removeTemporary = false
	return nil
}
