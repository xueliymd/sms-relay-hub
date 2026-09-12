package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
	"unicode"

	"github.com/google/uuid"
)

const (
	webConsoleAddress     = "127.0.0.1:35000"
	webConsoleIdleTimeout = 10 * time.Minute
	webTokenHeader        = "X-SMS-Relay-Token"
)

type webOptions struct {
	configPath    string
	stateDir      string
	listenAddress string
	servicePath   string
}

type webConfigView struct {
	Enabled                bool   `json:"enabled"`
	BaseURL                string `json:"base_url"`
	AllowInsecureLocalHTTP bool   `json:"allow_insecure_local_http"`
	TransportMode          string `json:"transport_mode"`
	FallbackToHTTP         bool   `json:"fallback_to_http"`
	MQTTBrokerURL          string `json:"mqtt_broker_url"`
	MQTTTopic              string `json:"mqtt_topic"`
	MQTTUsername           string `json:"mqtt_username"`
	HasMQTTPassword        bool   `json:"has_mqtt_password"`
	DeviceID               string `json:"device_id"`
	AndroidUserID          int    `json:"android_user_id"`
	PollIntervalSeconds    int    `json:"poll_interval_seconds"`
	MaxBackoffSeconds      int    `json:"max_backoff_seconds"`
	WatcherIdleSeconds     int    `json:"watcher_idle_seconds"`
	SMSDatabasePath        string `json:"sms_database_path"`
	HasHMACSecret          bool   `json:"has_hmac_secret"`
}

type webStatusView struct {
	State           string   `json:"state"`
	Detail          string   `json:"detail"`
	PendingCount    int      `json:"pending_count"`
	UpdatedAt       string   `json:"updated_at"`
	DatabasePath    string   `json:"database_path,omitempty"`
	DatabaseSource  string   `json:"database_source,omitempty"`
	DatabaseMaxID   int64    `json:"database_max_id,omitempty"`
	DatabaseColumns []string `json:"database_columns,omitempty"`
	DatabaseError   string   `json:"database_error,omitempty"`
	ReaderMode      string   `json:"reader_mode,omitempty"`
	DatabaseCursor  int64    `json:"database_cursor,omitempty"`
	DatabaseUpdated string   `json:"database_updated_at,omitempty"`
}

type webConfigUpdate struct {
	Enabled                bool   `json:"enabled"`
	BaseURL                string `json:"base_url"`
	AllowInsecureLocalHTTP bool   `json:"allow_insecure_local_http"`
	TransportMode          string `json:"transport_mode"`
	FallbackToHTTP         *bool  `json:"fallback_to_http"`
	MQTTBrokerURL          string `json:"mqtt_broker_url"`
	MQTTTopic              string `json:"mqtt_topic"`
	MQTTUsername           string `json:"mqtt_username"`
	MQTTPassword           string `json:"mqtt_password"`
	DeviceID               string `json:"device_id"`
	AndroidUserID          int    `json:"android_user_id"`
	PollIntervalSeconds    int    `json:"poll_interval_seconds"`
	MaxBackoffSeconds      int    `json:"max_backoff_seconds"`
	WatcherIdleSeconds     int    `json:"watcher_idle_seconds"`
	SMSDatabasePath        string `json:"sms_database_path"`
	ReplaceHMACSecret      bool   `json:"replace_hmac_secret"`
	HMACSecret             string `json:"hmac_secret"`
}

type webDatabaseRequest struct {
	Path          string `json:"path"`
	AndroidUserID int    `json:"android_user_id"`
}

type webDatabaseView struct {
	Valid     bool     `json:"valid"`
	Path      string   `json:"path,omitempty"`
	Source    string   `json:"source,omitempty"`
	ErrorCode string   `json:"error_code,omitempty"`
	MaxID     int64    `json:"max_id,omitempty"`
	Rows      int64    `json:"rows,omitempty"`
	Columns   []string `json:"columns,omitempty"`
}

type enrollmentQRPayload struct {
	Version   int    `json:"v"`
	Type      string `json:"type"`
	URL       string `json:"url"`
	DeviceID  string `json:"device_id"`
	Token     string `json:"token"`
	ExpiresAt string `json:"expires_at"`
}

type enrollmentRequest struct {
	QRText string `json:"qr_text"`
}

type enrollmentCredential struct {
	DeviceID   string `json:"device_id"`
	DeviceName string `json:"device_name"`
	ClientType string `json:"client_type"`
	AuthKind   string `json:"auth_kind"`
	HMACSecret string `json:"hmac_secret"`
}

type webConsoleHandler struct {
	configPath       string
	stateDir         string
	token            string
	expectedHost     string
	lastActive       atomic.Int64
	workerRunning    func() bool
	startService     func() error
	databaseProbe    func(context.Context, int, string) webDatabaseView
	enrollmentClient func(context.Context, enrollmentQRPayload) (enrollmentCredential, error)
}

func newWebConsoleHandler(configPath, stateDir, token, expectedHost string) *webConsoleHandler {
	handler := &webConsoleHandler{
		configPath:   configPath,
		stateDir:     stateDir,
		token:        token,
		expectedHost: expectedHost,
	}
	handler.workerRunning = func() bool { return workerProcessRunning(stateDir) }
	handler.databaseProbe = probeWebDatabase
	handler.enrollmentClient = exchangeEnrollment
	handler.touch()
	return handler
}

func (handler *webConsoleHandler) touch() {
	handler.lastActive.Store(time.Now().UnixNano())
}

func (handler *webConsoleHandler) lastActivity() time.Time {
	return time.Unix(0, handler.lastActive.Load())
}

func (handler *webConsoleHandler) ServeHTTP(response http.ResponseWriter, request *http.Request) {
	response.Header().Set("Cache-Control", "no-store")
	response.Header().Set("X-Content-Type-Options", "nosniff")
	response.Header().Set("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

	if request.URL.Path == "/" {
		if request.Host != handler.expectedHost || request.Method != http.MethodGet {
			http.Error(response, "request_rejected", http.StatusForbidden)
			return
		}
		handler.touch()
		response.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = io.WriteString(response, webConsoleHTML)
		return
	}

	if request.Header.Get(webTokenHeader) != handler.token {
		http.Error(response, "authentication_required", http.StatusUnauthorized)
		return
	}
	if request.Host != handler.expectedHost {
		http.Error(response, "request_rejected", http.StatusForbidden)
		return
	}
	handler.touch()

	switch request.URL.Path {
	case "/api/config":
		handler.handleConfig(response, request)
	case "/api/status":
		handler.handleStatus(response, request)
	case "/api/database/validate":
		handler.handleDatabaseValidate(response, request)
	case "/api/database/redetect":
		handler.handleDatabaseRedetect(response, request)
	case "/api/enrollment":
		handler.handleEnrollment(response, request)
	case "/api/heartbeat":
		if request.Method != http.MethodPost {
			http.Error(response, "method_not_allowed", http.StatusMethodNotAllowed)
			return
		}
		response.WriteHeader(http.StatusNoContent)
	default:
		http.NotFound(response, request)
	}
}

func (handler *webConsoleHandler) handleStatus(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		http.Error(response, "method_not_allowed", http.StatusMethodNotAllowed)
		return
	}
	view := webStatusView{State: "unknown"}
	if _, err := os.Stat(handler.configPath); errors.Is(err, os.ErrNotExist) {
		view.State = "not_configured"
	} else if _, err := os.Stat(filepath.Join(handler.stateDir, "disabled")); err == nil {
		view.State = "disabled"
	} else if handler.workerRunning != nil && !handler.workerRunning() {
		view.State = "worker_stopped"
	} else if status, err := readRuntimeStatus(handler.stateDir); err == nil {
		view.State = status.State
		view.Detail = status.Detail
		view.UpdatedAt = status.UpdatedAt
	}
	view.PendingCount = countPendingMessages(handler.stateDir)
	if diagnostic, err := readReaderDiagnostic(handler.stateDir); err == nil {
		view.DatabasePath = diagnostic.Path
		view.DatabaseSource = diagnostic.Source
		view.DatabaseMaxID = diagnostic.MaxID
		view.DatabaseColumns = diagnostic.Columns
		view.DatabaseError = diagnostic.ErrorCode
		view.ReaderMode = diagnostic.Mode
		view.DatabaseCursor = diagnostic.Cursor
		view.DatabaseUpdated = diagnostic.UpdatedAt
	}
	writeWebJSON(response, http.StatusOK, view)
}

func probeWebDatabase(ctx context.Context, userID int, databasePath string) webDatabaseView {
	if userID < 0 {
		return webDatabaseView{ErrorCode: "android_user_invalid"}
	}
	source := "manual"
	paths := []string{strings.TrimSpace(databasePath)}
	if paths[0] == "" {
		source = "automatic"
		paths = discoverSQLiteDatabases(userID)
	}
	if len(paths) == 0 {
		return webDatabaseView{Source: source, ErrorCode: "database_not_found"}
	}
	info, err := validateSQLiteDatabase(ctx, paths[0])
	if err != nil {
		return webDatabaseView{Path: paths[0], Source: source, ErrorCode: databaseErrorCode(err)}
	}
	return webDatabaseView{Valid: true, Path: info.Path, Source: source, MaxID: info.MaxID, Rows: info.Rows, Columns: info.Columns}
}

func databaseErrorCode(err error) string {
	if errors.Is(err, os.ErrNotExist) {
		return "database_not_found"
	}
	if errors.Is(err, os.ErrPermission) {
		return "database_permission_denied"
	}
	message := strings.ToLower(err.Error())
	switch {
	case strings.Contains(message, "required column"):
		return "sms_columns_missing"
	case strings.Contains(message, "invalid sms database path"), strings.Contains(message, "outside telephonyprovider"):
		return "database_path_invalid"
	default:
		return "database_open_failed"
	}
}

func (handler *webConsoleHandler) decodeDatabaseRequest(response http.ResponseWriter, request *http.Request) (webDatabaseRequest, bool) {
	if request.Method != http.MethodPost {
		http.Error(response, "method_not_allowed", http.StatusMethodNotAllowed)
		return webDatabaseRequest{}, false
	}
	expectedOrigin := "http://" + handler.expectedHost
	if origin := request.Header.Get("Origin"); origin != "" && origin != expectedOrigin {
		writeWebJSON(response, http.StatusForbidden, map[string]string{"error": "request_rejected"})
		return webDatabaseRequest{}, false
	}
	if !strings.HasPrefix(request.Header.Get("Content-Type"), "application/json") {
		writeWebJSON(response, http.StatusUnsupportedMediaType, map[string]string{"error": "json_required"})
		return webDatabaseRequest{}, false
	}
	decoder := json.NewDecoder(io.LimitReader(request.Body, 4097))
	decoder.DisallowUnknownFields()
	var value webDatabaseRequest
	if err := decoder.Decode(&value); err != nil || decoder.Decode(&struct{}{}) != io.EOF {
		writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "config_invalid"})
		return webDatabaseRequest{}, false
	}
	value.Path = strings.TrimSpace(value.Path)
	return value, true
}

func parseEnrollmentQR(raw string, now time.Time) (enrollmentQRPayload, error) {
	if strings.TrimSpace(raw) == "" || len(raw) > 4096 {
		return enrollmentQRPayload{}, fmt.Errorf("invalid enrollment QR text")
	}
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.DisallowUnknownFields()
	var payload enrollmentQRPayload
	if err := decoder.Decode(&payload); err != nil {
		return enrollmentQRPayload{}, fmt.Errorf("invalid enrollment QR text")
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return enrollmentQRPayload{}, fmt.Errorf("invalid enrollment QR text")
	}
	if payload.Version != 1 || payload.Type != "sms-relay-enroll" {
		return enrollmentQRPayload{}, fmt.Errorf("unsupported enrollment QR")
	}
	parsedURL, err := url.Parse(strings.TrimSpace(payload.URL))
	if err != nil || parsedURL.Host == "" || parsedURL.User != nil || parsedURL.RawQuery != "" || parsedURL.Fragment != "" {
		return enrollmentQRPayload{}, fmt.Errorf("invalid enrollment URL")
	}
	if parsedURL.Scheme != "http" && parsedURL.Scheme != "https" {
		return enrollmentQRPayload{}, fmt.Errorf("invalid enrollment URL")
	}
	deviceID, err := uuid.Parse(strings.TrimSpace(payload.DeviceID))
	if err != nil {
		return enrollmentQRPayload{}, fmt.Errorf("invalid enrollment device")
	}
	token := strings.TrimSpace(payload.Token)
	if len(token) < 32 || len(token) > 256 || strings.IndexFunc(token, unicode.IsSpace) >= 0 {
		return enrollmentQRPayload{}, fmt.Errorf("invalid enrollment token")
	}
	expiresAt, err := time.Parse(time.RFC3339, strings.TrimSpace(payload.ExpiresAt))
	if err != nil || !expiresAt.After(now) {
		return enrollmentQRPayload{}, fmt.Errorf("enrollment QR expired")
	}
	payload.URL = strings.TrimRight(parsedURL.String(), "/")
	payload.DeviceID = deviceID.String()
	payload.Token = token
	payload.ExpiresAt = expiresAt.UTC().Format(time.RFC3339)
	return payload, nil
}

func exchangeEnrollment(ctx context.Context, payload enrollmentQRPayload) (enrollmentCredential, error) {
	body, err := json.Marshal(map[string]string{
		"device_id":        payload.DeviceID,
		"enrollment_token": payload.Token,
	})
	if err != nil {
		return enrollmentCredential{}, err
	}
	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		strings.TrimRight(payload.URL, "/")+"/api/v1/enrollment/consume",
		bytes.NewReader(body),
	)
	if err != nil {
		return enrollmentCredential{}, err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Content-Type", "application/json")
	client := &http.Client{
		Timeout: 15 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	response, err := client.Do(request)
	if err != nil {
		return enrollmentCredential{}, err
	}
	defer response.Body.Close()
	rawResponse, err := io.ReadAll(io.LimitReader(response.Body, 16*1024+1))
	if err != nil || len(rawResponse) > 16*1024 {
		return enrollmentCredential{}, fmt.Errorf("enrollment response unavailable")
	}
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return enrollmentCredential{}, fmt.Errorf("enrollment exchange returned %d", response.StatusCode)
	}
	decoder := json.NewDecoder(bytes.NewReader(rawResponse))
	decoder.DisallowUnknownFields()
	var credential enrollmentCredential
	if err := decoder.Decode(&credential); err != nil || decoder.Decode(&struct{}{}) != io.EOF {
		return enrollmentCredential{}, fmt.Errorf("invalid enrollment response")
	}
	deviceID, err := uuid.Parse(strings.TrimSpace(credential.DeviceID))
	if err != nil || deviceID.String() != payload.DeviceID || credential.ClientType != "root_module" || credential.AuthKind != "hmac" {
		return enrollmentCredential{}, fmt.Errorf("enrollment credential mismatch")
	}
	secret, err := normalizeHMACSecret(credential.HMACSecret)
	if err != nil {
		return enrollmentCredential{}, fmt.Errorf("invalid enrollment credential")
	}
	credential.DeviceID = deviceID.String()
	credential.HMACSecret = secret
	return credential, nil
}

func (handler *webConsoleHandler) handleEnrollment(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		http.Error(response, "method_not_allowed", http.StatusMethodNotAllowed)
		return
	}
	expectedOrigin := "http://" + handler.expectedHost
	if origin := request.Header.Get("Origin"); origin != "" && origin != expectedOrigin {
		writeWebJSON(response, http.StatusForbidden, map[string]string{"error": "request_rejected"})
		return
	}
	if !strings.HasPrefix(request.Header.Get("Content-Type"), "application/json") {
		writeWebJSON(response, http.StatusUnsupportedMediaType, map[string]string{"error": "json_required"})
		return
	}
	decoder := json.NewDecoder(io.LimitReader(request.Body, 4097))
	decoder.DisallowUnknownFields()
	var enrollment enrollmentRequest
	if err := decoder.Decode(&enrollment); err != nil || decoder.Decode(&struct{}{}) != io.EOF {
		writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "enrollment_invalid"})
		return
	}
	payload, err := parseEnrollmentQR(enrollment.QRText, time.Now().UTC())
	if err != nil {
		writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "enrollment_invalid"})
		return
	}
	if handler.enrollmentClient == nil {
		writeWebJSON(response, http.StatusBadGateway, map[string]string{"error": "enrollment_exchange_failed"})
		return
	}
	credential, err := handler.enrollmentClient(request.Context(), payload)
	if err != nil {
		writeWebJSON(response, http.StatusBadGateway, map[string]string{"error": "enrollment_exchange_failed"})
		return
	}
	current, err := loadWebConsoleConfig(handler.configPath)
	if err != nil {
		writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "config_unavailable"})
		return
	}
	secret, err := normalizeHMACSecret(credential.HMACSecret)
	if err != nil {
		writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "credential_invalid"})
		return
	}
	deviceID, err := uuid.Parse(strings.TrimSpace(credential.DeviceID))
	if err != nil || deviceID.String() != payload.DeviceID || credential.ClientType != "root_module" || credential.AuthKind != "hmac" {
		writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "credential_invalid"})
		return
	}
	parsedURL, err := url.Parse(payload.URL)
	if err != nil {
		writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "enrollment_invalid"})
		return
	}
	candidate := current
	candidate.SchemaVersion = 2
	candidate.Source = "root_module"
	candidate.BaseURL = payload.URL
	candidate.DeviceID = deviceID.String()
	candidate.HMACSecret = secret
	candidate.Enabled = true
	candidate.AllowInsecureLocalHTTP = current.AllowInsecureLocalHTTP || parsedURL.Scheme == "http"
	candidate.InitialSyncMode = "new_only"
	candidate.WatcherIdleSeconds = effectiveWatcherIdleSeconds(current.WatcherIdleSeconds)
	if err := validateConfig(candidate); err != nil {
		writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "config_invalid"})
		return
	}
	if err := writeConfigAtomic(handler.configPath, candidate); err != nil {
		writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "config_write_failed"})
		return
	}
	if err := writePrivateMarker(filepath.Join(handler.stateDir, "run", "config.changed")); err != nil {
		writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "reload_signal_failed"})
		return
	}
	disabledMarker := filepath.Join(handler.stateDir, "disabled")
	if err := os.Remove(disabledMarker); err != nil && !errors.Is(err, os.ErrNotExist) {
		writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "state_update_failed"})
		return
	}
	if handler.startService != nil && handler.workerRunning != nil && !handler.workerRunning() {
		if err := handler.startService(); err != nil {
			_ = writePrivateMarker(disabledMarker)
			writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "service_start_failed"})
			return
		}
	}
	wakeWorkerWait(handler.stateDir)
	writeWebJSON(response, http.StatusOK, configToWebView(candidate))
}

func (handler *webConsoleHandler) handleDatabaseValidate(response http.ResponseWriter, request *http.Request) {
	value, ok := handler.decodeDatabaseRequest(response, request)
	if !ok {
		return
	}
	result := handler.databaseProbe(request.Context(), value.AndroidUserID, value.Path)
	status := http.StatusOK
	if !result.Valid {
		status = http.StatusUnprocessableEntity
	}
	writeWebJSON(response, status, result)
}

func (handler *webConsoleHandler) handleDatabaseRedetect(response http.ResponseWriter, request *http.Request) {
	value, ok := handler.decodeDatabaseRequest(response, request)
	if !ok {
		return
	}
	result := handler.databaseProbe(request.Context(), value.AndroidUserID, "")
	if !result.Valid {
		writeWebJSON(response, http.StatusUnprocessableEntity, result)
		return
	}
	if _, err := os.Stat(handler.configPath); errors.Is(err, os.ErrNotExist) {
		writeWebJSON(response, http.StatusOK, result)
		return
	}
	current, err := loadWebConsoleConfig(handler.configPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			writeWebJSON(response, http.StatusOK, result)
			return
		}
		writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "config_unavailable"})
		return
	}
	current.SMSDatabasePath = ""
	current.AndroidUserID = value.AndroidUserID
	if err := writeConfigAtomic(handler.configPath, current); err != nil {
		writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "config_write_failed"})
		return
	}
	_ = writePrivateMarker(filepath.Join(handler.stateDir, "run", "config.changed"))
	wakeWorkerWait(handler.stateDir)
	writeWebJSON(response, http.StatusOK, result)
}

func (handler *webConsoleHandler) handleConfig(response http.ResponseWriter, request *http.Request) {
	switch request.Method {
	case http.MethodGet:
		config, err := loadWebConsoleConfig(handler.configPath)
		if err != nil {
			writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "config_unavailable"})
			return
		}
		writeWebJSON(response, http.StatusOK, configToWebView(config))
	case http.MethodPost:
		expectedOrigin := "http://" + handler.expectedHost
		if origin := request.Header.Get("Origin"); origin != "" && origin != expectedOrigin {
			writeWebJSON(response, http.StatusForbidden, map[string]string{"error": "request_rejected"})
			return
		}
		if !strings.HasPrefix(request.Header.Get("Content-Type"), "application/json") {
			writeWebJSON(response, http.StatusUnsupportedMediaType, map[string]string{"error": "json_required"})
			return
		}
		decoder := json.NewDecoder(io.LimitReader(request.Body, 16*1024+1))
		decoder.DisallowUnknownFields()
		var update webConfigUpdate
		if err := decoder.Decode(&update); err != nil {
			writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "config_invalid"})
			return
		}
		if err := decoder.Decode(&struct{}{}); err != io.EOF {
			writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "config_invalid"})
			return
		}
		current, err := loadWebConsoleConfig(handler.configPath)
		if err != nil {
			writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "config_unavailable"})
			return
		}
		normalizedBaseURL, normalizeErr := normalizeBaseURL(update.BaseURL, update.AllowInsecureLocalHTTP)
		if normalizeErr != nil {
			writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "config_invalid"})
			return
		}
		fallbackToHTTP := current.FallbackToHTTP
		if update.FallbackToHTTP != nil {
			fallbackToHTTP = *update.FallbackToHTTP
		}
		candidate := rootConfig{
			SchemaVersion:          current.SchemaVersion,
			Enabled:                update.Enabled,
			BaseURL:                normalizedBaseURL,
			AllowInsecureLocalHTTP: update.AllowInsecureLocalHTTP,
			TransportMode:          strings.TrimSpace(update.TransportMode),
			FallbackToHTTP:         fallbackToHTTP,
			MQTTBrokerURL:          strings.TrimSpace(update.MQTTBrokerURL),
			MQTTTopic:              strings.TrimSpace(update.MQTTTopic),
			MQTTUsername:           strings.TrimSpace(update.MQTTUsername),
			MQTTPassword:           current.MQTTPassword,
			DeviceID:               strings.TrimSpace(update.DeviceID),
			HMACSecret:             current.HMACSecret,
			Source:                 current.Source,
			AndroidUserID:          update.AndroidUserID,
			InitialSyncMode:        current.InitialSyncMode,
			PollIntervalSeconds:    update.PollIntervalSeconds,
			MaxBackoffSeconds:      update.MaxBackoffSeconds,
			WatcherIdleSeconds:     effectiveWatcherIdleSeconds(update.WatcherIdleSeconds),
			SMSDatabasePath:        strings.TrimSpace(update.SMSDatabasePath),
		}
		if update.ReplaceHMACSecret {
			normalizedSecret, err := normalizeHMACSecret(update.HMACSecret)
			if err != nil {
				writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "credential_invalid"})
				return
			}
			candidate.HMACSecret = normalizedSecret
		} else if update.HMACSecret != "" {
			writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "credential_confirmation_required"})
			return
		}
		if update.MQTTPassword != "" {
			candidate.MQTTPassword = update.MQTTPassword
		}
		if err := validateConfig(candidate); err != nil {
			writeWebJSON(response, http.StatusUnprocessableEntity, map[string]string{"error": "config_invalid"})
			return
		}
		if candidate.SMSDatabasePath != "" {
			validated := handler.databaseProbe(request.Context(), candidate.AndroidUserID, candidate.SMSDatabasePath)
			if !validated.Valid {
				writeWebJSON(response, http.StatusUnprocessableEntity, validated)
				return
			}
		}
		if err := writeConfigAtomic(handler.configPath, candidate); err != nil {
			writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "config_write_failed"})
			return
		}
		if err := writePrivateMarker(filepath.Join(handler.stateDir, "run", "config.changed")); err != nil {
			writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "reload_signal_failed"})
			return
		}
		disabledMarker := filepath.Join(handler.stateDir, "disabled")
		if candidate.Enabled {
			_, markerErr := os.Stat(disabledMarker)
			wasDisabled := markerErr == nil
			if err := os.Remove(disabledMarker); err != nil && !errors.Is(err, os.ErrNotExist) {
				writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "state_update_failed"})
				return
			}
			if handler.startService != nil && (wasDisabled || handler.workerRunning != nil && !handler.workerRunning()) {
				if err := handler.startService(); err != nil {
					_ = writePrivateMarker(disabledMarker)
					writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "service_start_failed"})
					return
				}
			}
		} else if err := writePrivateMarker(disabledMarker); err != nil {
			writeWebJSON(response, http.StatusInternalServerError, map[string]string{"error": "state_update_failed"})
			return
		}
		wakeWorkerWait(handler.stateDir)
		writeWebJSON(response, http.StatusOK, configToWebView(candidate))
	default:
		http.Error(response, "method_not_allowed", http.StatusMethodNotAllowed)
	}
}

func configToWebView(config rootConfig) webConfigView {
	return webConfigView{
		Enabled:                config.Enabled,
		BaseURL:                config.BaseURL,
		AllowInsecureLocalHTTP: config.AllowInsecureLocalHTTP,
		TransportMode:          effectiveTransportMode(config.TransportMode),
		FallbackToHTTP:         config.FallbackToHTTP,
		MQTTBrokerURL:          config.MQTTBrokerURL,
		MQTTTopic:              config.MQTTTopic,
		MQTTUsername:           config.MQTTUsername,
		HasMQTTPassword:        config.MQTTPassword != "",
		DeviceID:               config.DeviceID,
		AndroidUserID:          config.AndroidUserID,
		PollIntervalSeconds:    config.PollIntervalSeconds,
		MaxBackoffSeconds:      config.MaxBackoffSeconds,
		WatcherIdleSeconds:     effectiveWatcherIdleSeconds(config.WatcherIdleSeconds),
		SMSDatabasePath:        config.SMSDatabasePath,
		HasHMACSecret:          config.HMACSecret != "",
	}
}

func bootstrapRootConfig() rootConfig {
	return rootConfig{
		SchemaVersion:       2,
		Source:              "root_module",
		AndroidUserID:       0,
		InitialSyncMode:     "new_only",
		TransportMode:       "http",
		FallbackToHTTP:      true,
		PollIntervalSeconds: 3,
		MaxBackoffSeconds:   300,
		WatcherIdleSeconds:  1,
		SMSDatabasePath:     "",
	}
}

func effectiveTransportMode(value string) string {
	if value == "" {
		return "http"
	}
	return value
}

func effectiveWatcherIdleSeconds(value int) int {
	if value < 1 {
		return 1
	}
	return value
}

func normalizeHMACSecret(value string) (string, error) {
	normalized := strings.TrimRight(strings.TrimSpace(value), "=")
	if _, err := decodeSecret(normalized); err != nil {
		return "", err
	}
	return normalized, nil
}

func loadWebConsoleConfig(path string) (rootConfig, error) {
	config, err := loadConfig(path)
	if err == nil {
		return config, nil
	}
	if errors.Is(err, os.ErrNotExist) {
		return bootstrapRootConfig(), nil
	}
	return rootConfig{}, err
}

func writeWebJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json; charset=utf-8")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}

func writeConfigAtomic(path string, config rootConfig) error {
	raw, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	return writePrivateFileAtomic(path, raw)
}

func writePrivateMarker(path string) error {
	return writePrivateFileAtomic(path, nil)
}

func wakeWorkerWait(stateDir string) {
	if runtime.GOOS == "windows" {
		return
	}
	rawPID, err := os.ReadFile(filepath.Join(stateDir, "run", "worker.pid"))
	if err != nil {
		return
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(rawPID)))
	if err != nil || pid <= 0 {
		return
	}
	process, err := os.FindProcess(pid)
	if err != nil {
		return
	}
	_ = process.Signal(syscall.Signal(10))
}

func readRuntimeStatus(stateDir string) (webStatusView, error) {
	path := filepath.Join(stateDir, "run", "status.json")
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || runtime.GOOS != "windows" && info.Mode().Perm()&0o077 != 0 || info.Size() > 1024 {
		return webStatusView{}, fmt.Errorf("runtime status unavailable")
	}
	file, err := os.Open(path)
	if err != nil {
		return webStatusView{}, err
	}
	defer file.Close()
	decoder := json.NewDecoder(io.LimitReader(file, 1024))
	decoder.DisallowUnknownFields()
	var status webStatusView
	if err := decoder.Decode(&status); err != nil {
		return webStatusView{}, err
	}
	if status.State == "" || status.Detail != "" && len(status.Detail) > 64 || len(status.UpdatedAt) > 64 {
		return webStatusView{}, fmt.Errorf("runtime status invalid")
	}
	return status, nil
}

func countRegularJSON(directory string) int {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return 0
	}
	count := 0
	for _, entry := range entries {
		if entry.Type().IsRegular() && strings.HasSuffix(entry.Name(), ".json") {
			count++
		}
	}
	return count
}

func countPendingMessages(stateDir string) int {
	count := countRegularJSON(filepath.Join(stateDir, "spool"))
	inflightEntries, err := os.ReadDir(filepath.Join(stateDir, "run", "inflight"))
	if err != nil {
		return count
	}
	for _, entry := range inflightEntries {
		if entry.IsDir() {
			count += countRegularJSON(filepath.Join(stateDir, "run", "inflight", entry.Name()))
		}
	}
	return count
}

func workerProcessRunning(stateDir string) bool {
	rawPID, err := os.ReadFile(filepath.Join(stateDir, "run", "worker.pid"))
	if err != nil {
		return false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(rawPID)))
	if err != nil || pid <= 0 {
		return false
	}
	cmdline, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "cmdline"))
	return err == nil && strings.Contains(strings.ReplaceAll(string(cmdline), "\x00", " "), "service.sh")
}

func startServiceProcess(servicePath, stateDir string) error {
	if !filepath.IsAbs(servicePath) || filepath.Base(servicePath) != "service.sh" {
		return fmt.Errorf("invalid service path")
	}
	command := exec.Command("/system/bin/sh", servicePath)
	command.Stdin = nil
	command.Stdout = nil
	command.Stderr = nil
	if err := command.Start(); err != nil {
		return err
	}
	if err := command.Process.Release(); err != nil {
		return err
	}
	for attempt := 0; attempt < 30; attempt++ {
		if workerProcessRunning(stateDir) {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("service did not start")
}

func writePrivateFileAtomic(path string, content []byte) error {
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return err
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(directory, ".sms-relay-web-*")
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
	if _, err := temporary.Write(content); err != nil {
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
	return os.Chmod(path, 0o600)
}

func validateWebListenAddress(address string) error {
	if address != webConsoleAddress {
		return fmt.Errorf("web console must use loopback port 35000")
	}
	return nil
}

func parseWebOptions(args []string) (webOptions, error) {
	if len(args) != 8 {
		return webOptions{}, fmt.Errorf("invalid web arguments")
	}
	values := make(map[string]string, 4)
	for index := 0; index < len(args); index += 2 {
		name := args[index]
		if name != "--config" && name != "--state-dir" && name != "--listen" && name != "--service" {
			return webOptions{}, fmt.Errorf("unknown web argument")
		}
		if _, exists := values[name]; exists {
			return webOptions{}, fmt.Errorf("duplicate web argument")
		}
		values[name] = args[index+1]
	}
	if values["--config"] == "" || values["--state-dir"] == "" || values["--service"] == "" {
		return webOptions{}, fmt.Errorf("missing web path")
	}
	if err := validateWebListenAddress(values["--listen"]); err != nil {
		return webOptions{}, err
	}
	return webOptions{
		configPath:    values["--config"],
		stateDir:      values["--state-dir"],
		listenAddress: values["--listen"],
		servicePath:   values["--service"],
	}, nil
}

func newWebSessionToken() (string, error) {
	raw := make([]byte, 32)
	if _, err := io.ReadFull(rand.Reader, raw); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(raw), nil
}

func webTokenPath(stateDir string) string {
	return filepath.Join(stateDir, "run", "web.token")
}

func readWebToken(stateDir string) (string, error) {
	path := webTokenPath(stateDir)
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 || info.Size() > 256 {
		return "", fmt.Errorf("web token unavailable")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	token := strings.TrimSpace(string(raw))
	if token == "" {
		return "", fmt.Errorf("web token unavailable")
	}
	return token, nil
}

func runWebConsole(options webOptions) error {
	if _, err := loadWebConsoleConfig(options.configPath); err != nil {
		return err
	}
	if err := validateWebListenAddress(options.listenAddress); err != nil {
		return err
	}
	listener, err := net.Listen("tcp4", options.listenAddress)
	if err != nil {
		return err
	}
	token, err := newWebSessionToken()
	if err != nil {
		_ = listener.Close()
		return err
	}
	if err := writePrivateFileAtomic(webTokenPath(options.stateDir), []byte(token+"\n")); err != nil {
		_ = listener.Close()
		return err
	}
	defer os.Remove(webTokenPath(options.stateDir))
	handler := newWebConsoleHandler(options.configPath, options.stateDir, token, options.listenAddress)
	handler.startService = func() error { return startServiceProcess(options.servicePath, options.stateDir) }
	webPIDPath := filepath.Join(options.stateDir, "run", "web.pid")
	if err := writePrivateFileAtomic(webPIDPath, []byte(strconv.Itoa(os.Getpid())+"\n")); err != nil {
		_ = listener.Close()
		return err
	}
	defer os.Remove(webPIDPath)
	return serveWebConsole(listener, handler, webConsoleIdleTimeout)
}

func serveWebConsole(listener net.Listener, handler *webConsoleHandler, idleTimeout time.Duration) error {
	server := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       30 * time.Second,
		MaxHeaderBytes:    8 * 1024,
	}
	serveResult := make(chan error, 1)
	go func() {
		serveResult <- server.Serve(listener)
	}()
	checkInterval := idleTimeout / 4
	if checkInterval < 10*time.Millisecond {
		checkInterval = 10 * time.Millisecond
	}
	if checkInterval > time.Second {
		checkInterval = time.Second
	}
	ticker := time.NewTicker(checkInterval)
	defer ticker.Stop()
	for {
		select {
		case err := <-serveResult:
			if errors.Is(err, http.ErrServerClosed) {
				return nil
			}
			return err
		case <-ticker.C:
			if time.Since(handler.lastActivity()) >= idleTimeout {
				_ = server.Close()
				err := <-serveResult
				if errors.Is(err, http.ErrServerClosed) {
					return nil
				}
				return err
			}
		}
	}
}

const webConsoleHTML = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>SMS Relay 模块设置</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#eef2f7;color-scheme:light}*{box-sizing:border-box}body{margin:0;padding:0}.shell{max-width:760px;margin:0 auto;padding:0 16px 28px}h1{font-size:21px;margin:0}h2{font-size:16px;margin:24px 0 10px}#status{font-size:12px;color:#526173}.console-top{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.98);border-bottom:1px solid #d8e0ec;box-shadow:0 4px 14px rgba(23,32,51,.06)}.console-header{display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:60px}.console-header strong{font-size:21px}.console-tabs{display:flex;gap:8px;overflow-x:auto;padding:10px 0 14px;scrollbar-width:none}.console-tabs::-webkit-scrollbar{display:none}.console-tab{flex:1 0 84px;white-space:nowrap;background:#fff;color:#415067;box-shadow:inset 0 0 0 1px #c9d2df}.console-tab.active{color:#fff;background:#2457c5;box-shadow:none}.status-card{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center;margin:16px 0;padding:14px;border:1px solid #d8e0ec;border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(23,32,51,.04)}.status-label{display:block;font-size:12px;color:#526173}.status-value{font-size:15px}.status-card strong{font-size:22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}label{display:grid;gap:6px;font-weight:650;font-size:14px}.wide{grid-column:1/-1}input,textarea{width:100%;min-height:44px;border:1px solid #bdc6d4;border-radius:8px;background:#fff;padding:10px 12px;font:inherit}textarea{resize:vertical}.qr-panel{border:1px solid #c9d5ee;border-radius:10px;background:#f7faff;padding:14px;display:grid;gap:10px}.qr-panel strong{font-size:16px}.qr-panel video{width:100%;max-height:280px;border-radius:10px;background:#172033}.qr-panel input[type=file]{display:none}small,.hint,.diagnostic{color:#526173;font-size:12px;line-height:1.5}.console-panel{display:none}.console-panel.active{display:grid;gap:16px}.console-card{border:1px solid #dbe2ec;background:#fff;border-radius:12px;padding:16px;box-shadow:0 2px 8px rgba(23,32,51,.04)}.advanced{border:0;padding:0;background:transparent}.advanced summary{cursor:pointer;font-weight:750;font-size:16px}.advanced>.console-card{margin-top:12px}.toggle{display:flex;align-items:center;gap:9px;min-height:44px}.toggle input{width:18px;min-height:18px}.button-row{display:flex;gap:10px}.button-row button{background:#526173}.actions{position:sticky;bottom:0;z-index:25;display:flex;justify-content:flex-end;margin-top:18px;padding:12px 0;background:rgba(238,242,247,.97);backdrop-filter:blur(8px)}button{min-height:44px;border:0;border-radius:8px;background:#2457c5;color:#fff;padding:0 18px;font-weight:700;font-size:15px}button:disabled{opacity:.55}@media(max-width:600px){.shell{padding:0 12px 24px}.console-header{min-height:54px}.console-tab{flex:0 0 84px;font-size:13px}.status-card{grid-template-columns:1fr}.status-card strong{text-align:right}.grid{grid-template-columns:1fr}.wide{grid-column:auto}.button-row{display:grid}.actions{padding:10px 0 14px}}
</style>
<style>.transport-panel{border:1px solid #9fb6e8;border-radius:10px;background:#f8fbff;padding:16px;display:grid;gap:12px}.transport-title{display:flex;align-items:center;gap:8px;color:#163f96}.transport-title svg{width:20px;height:20px}.transport-note{margin:0;color:#526173;font-size:12px;line-height:1.5}.transport-panel select{width:100%;min-height:44px;border:1px solid #bdc6d4;border-radius:6px;background:white;padding:10px 12px;font:inherit}.transport-panel details{border-top:1px solid #d8e0ec;padding-top:10px}.transport-panel summary{cursor:pointer;font-weight:700}</style>
</head>
<body><main class="shell"><div class="console-top"><header class="console-header"><strong>SMS Relay 模块设置</strong><span id="status">正在连接</span></header>
<nav class="console-tabs" aria-label="配置分区"><button class="console-tab active" type="button" data-tab="quick">快速配置</button><button class="console-tab" type="button" data-tab="transport">上报通道</button><button class="console-tab" type="button" data-tab="advanced">高级诊断</button></nav></div>
<section class="status-card" aria-live="polite"><div><span class="status-label">服务状态</span><strong class="status-value" id="service_status">正在读取</strong></div><div><span class="status-label">待处理消息</span><strong id="pending_count">--</strong></div></section>
<form id="config">
<section class="console-panel active" id="panel-quick" aria-label="快速配置"><div class="console-card"><div class="grid">
<label class="wide">接收平台地址<input id="base_url" autocomplete="url" required></label>
<section class="wide qr-panel"><strong>扫码导入后台设备配置</strong><p class="hint">在网页管理端设备页为 Root 模块生成二维码，然后在这里扫描。二维码仅一次有效，默认 5 分钟过期；不支持相机时可粘贴二维码内容或选择二维码图片。</p><div class="button-row"><button id="scan_qr" type="button">扫描二维码</button><button id="stop_qr" type="button">停止扫描</button><button id="choose_qr_image" type="button">选择二维码图片</button></div><video id="qr_video" autoplay playsinline hidden></video><input id="qr_image" type="file" accept="image/*" capture="environment"><textarea id="qr_text" rows="3" placeholder="也可以粘贴后台二维码内容"></textarea><button id="import_qr" type="button">导入二维码配置</button></section>
<label class="wide">设备 ID<input id="device_id" autocomplete="off" required></label>
<label class="toggle"><input id="enabled" type="checkbox">启用短信转发</label>
<label class="toggle"><input id="allow_insecure_local_http" type="checkbox">允许本地私网 HTTP</label>
<label class="wide">HMAC 密钥（首次设置或替换）<input id="hmac_secret" type="password" autocomplete="new-password" placeholder="已配置时留空保持不变"></label>
<label class="toggle wide"><input id="replace_hmac_secret" type="checkbox">确认设置或替换 HMAC 密钥</label>
</div></div></section>
<section class="console-panel" id="panel-transport" aria-label="上报通道"><div class="console-card"><div class="grid">
<section class="wide transport-panel"><strong class="transport-title"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 12h16M12 4v16M7 7l10 10M17 7 7 17"/></svg>短信上报通道</strong><p class="transport-note">HTTP 是默认可靠通道；WS 适合低延迟长连接；MQTT 适合模块化设备。WS/MQTT 失败时可自动回到 HTTP，短信仍保留在本地队列。</p><label>首选协议<select id="transport_mode"><option value="http">HTTP（推荐，稳定）</option><option value="ws">WebSocket（低延迟）</option><option value="mqtt">MQTT（需要 Broker）</option></select></label><label class="toggle"><input id="fallback_to_http" type="checkbox" checked>WS/MQTT 失败自动回退 HTTP</label><details><summary>MQTT 连接参数（仅选择 MQTT 时填写）</summary><div class="grid"><label class="wide">Broker 地址<input id="mqtt_broker_url" autocomplete="url" placeholder="mqtts://broker.example:8883"></label><label class="wide">主题（留空自动生成）<input id="mqtt_topic" autocomplete="off" placeholder="relay/v1/devices/{device_id}/messages"></label><label>用户名<input id="mqtt_username" autocomplete="username"></label><label>密码<input id="mqtt_password" type="password" autocomplete="new-password" placeholder="已配置时留空"></label></div></details></section>
</div></div></section>
<section class="console-panel" id="panel-advanced" aria-label="高级诊断">
<details class="advanced"><summary>高级设置（机型兼容与延迟排查）</summary><div class="console-card"><p class="hint">普通情况下保持自动检测。只有自动检测失败或 ROM 数据库位置特殊时，才需要手动填写数据库路径。</p><h2>数据库</h2><div class="grid"><label class="wide">手动数据库路径（留空表示自动检测）<input id="sms_database_path" autocomplete="off" placeholder="例如 /data/user/0/com.android.providers.telephony/databases/mmssms.db"></label><div class="button-row wide"><button id="validate_database" type="button">验证数据库</button><button id="redetect_database" type="button">重新自动检测</button></div><div class="diagnostic wide" id="database_diagnostic">数据库状态：等待读取</div></div><h2>读取与唤醒</h2><div class="grid"><label>Android 用户 ID<input id="android_user_id" type="number" min="0" required><small>主用户通常是 0；工作资料可能是其他用户空间。</small></label><label>监听空闲等待秒数<input id="watcher_idle_seconds" type="number" min="1" max="3600" required><small>监听器正常时的休眠时间；收到数据库变化会立即唤醒。</small></label><label>补偿扫描秒数<input id="poll_interval_seconds" type="number" min="1" max="3600" required><small>监听器不可用时的主动扫描间隔。</small></label></div><h2>网络重试</h2><div class="grid"><label>上传失败最大等待秒数<input id="max_backoff_seconds" type="number" min="1" max="3600" required><small>只影响断网或服务器失败重试，不延迟新短信第一次上传。</small></label></div><h2>运行诊断</h2><div class="diagnostic" id="runtime_diagnostic">等待读取诊断信息</div></div></details></section><div class="actions"><button id="save" type="submit">保存并热生效</button></div></form></main>
<script>
document.querySelectorAll('.console-tab').forEach(tab=>tab.addEventListener('click',()=>{const name=tab.dataset.tab;document.querySelectorAll('.console-tab').forEach(item=>item.classList.toggle('active',item===tab));document.querySelectorAll('.console-panel').forEach(panel=>panel.classList.toggle('active',panel.id==='panel-'+name))}));
const statusNode=document.getElementById('status');
const serviceStatusNode=document.getElementById('service_status');
const pendingCountNode=document.getElementById('pending_count');
const fragment=location.hash.slice(1);if(fragment)sessionStorage.setItem('sms-relay-web-token',fragment);
history.replaceState(null,'',location.pathname);const token=sessionStorage.getItem('sms-relay-web-token')||'';
const headers={'X-SMS-Relay-Token':token};
const setStatus=(text)=>{statusNode.textContent=text};
const errorLabels={credential_confirmation_required:'请输入 HMAC 密钥并勾选“确认设置或替换 HMAC 密钥”',credential_invalid:'HMAC 密钥格式错误：请粘贴后台为这台设备生成的完整密钥',enrollment_invalid:'二维码无效、已过期或不是 Root 模块配置二维码，请重新生成',enrollment_exchange_failed:'二维码兑换失败：请确认手机能访问平台地址，并重新生成二维码',config_invalid:'配置参数无效：请检查平台地址、设备 UUID 和时间参数',database_not_found:'数据库不存在：请留空让模块自动检测，或填写正确的 mmssms.db 路径',database_permission_denied:'数据库无权限读取：请确认模块已获得 Root 权限',sms_columns_missing:'数据库缺少短信字段：当前路径不是系统短信数据库',database_open_failed:'数据库无法打开：请更换路径或先停止占用数据库的进程',config_write_failed:'配置文件写入失败：请检查 /data/adb/sms-relay 权限和剩余空间',reload_signal_failed:'配置已写入，但服务热重载信号失败，请重启模块',state_update_failed:'配置已写入，但服务状态更新失败，请重启模块',service_start_failed:'配置已写入，但短信服务启动失败，请重启模块'};
function explainError(error){const code=error instanceof Error?error.message:'request_failed';return '保存失败：'+(errorLabels[code]||('请求失败：'+code))}
async function api(path,options={}){const response=await fetch(path,{...options,headers:{...headers,...(options.headers||{})}});if(!response.ok){let code='http_'+response.status;try{const value=await response.json();code=value.error||value.error_code||code}catch{}throw new Error(code)}return response.status===204?null:response.json()}
let qrStream=null;let qrDetector=null;let qrScanTimer=0;
function stopQR(){if(qrScanTimer){clearTimeout(qrScanTimer);qrScanTimer=0}if(qrStream){qrStream.getTracks().forEach(track=>track.stop());qrStream=null}const video=document.getElementById('qr_video');video.pause();video.srcObject=null;video.hidden=true}
async function importQRText(text){const value=String(text||'').trim();if(!value){setStatus('请粘贴或扫描后台二维码内容');return}stopQR();setStatus('正在验证二维码并获取设备配置');try{const saved=await api('/api/enrollment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({qr_text:value})});document.getElementById('base_url').value=saved.base_url||'';document.getElementById('device_id').value=saved.device_id||'';document.getElementById('allow_insecure_local_http').checked=Boolean(saved.allow_insecure_local_http);document.getElementById('enabled').checked=Boolean(saved.enabled);document.getElementById('hmac_secret').value='';document.getElementById('replace_hmac_secret').checked=false;document.getElementById('qr_text').value='';setStatus('扫码配置成功，已热生效')}catch(error){setStatus('扫码配置失败：'+(errorLabels[error.message]||error.message||'请求失败'))}}
async function detectQRFrame(){if(!qrDetector||!qrStream)return;const video=document.getElementById('qr_video');try{if(video.readyState>=2){const results=await qrDetector.detect(video);if(results.length&&results[0].rawValue){await importQRText(results[0].rawValue);return}}}catch{}qrScanTimer=setTimeout(detectQRFrame,250)}
async function scanQR(){if(!('BarcodeDetector' in window)){setStatus('当前浏览器不支持相机扫码，请粘贴内容或选择二维码图片');return}try{qrDetector=new BarcodeDetector({formats:['qr_code']});qrStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});const video=document.getElementById('qr_video');video.srcObject=qrStream;video.hidden=false;await video.play();setStatus('请将后台二维码放入取景框');detectQRFrame()}catch{stopQR();setStatus('无法打开相机，请检查权限或改用二维码图片/粘贴内容')}}
async function readQRImage(file){if(!file)return;if(!qrDetector){if(!('BarcodeDetector' in window)){setStatus('当前浏览器不支持图片扫码，请粘贴二维码内容');return}try{qrDetector=new BarcodeDetector({formats:['qr_code']})}catch{setStatus('当前浏览器不支持二维码识别，请粘贴二维码内容');return}}try{const bitmap=await createImageBitmap(file);const results=await qrDetector.detect(bitmap);bitmap.close();if(results.length&&results[0].rawValue){await importQRText(results[0].rawValue)}else setStatus('图片中没有识别到二维码')}catch{setStatus('二维码图片读取失败，请改用相机或粘贴内容')}}
document.getElementById('scan_qr').addEventListener('click',scanQR);document.getElementById('stop_qr').addEventListener('click',()=>{stopQR();setStatus('已停止扫码')});document.getElementById('choose_qr_image').addEventListener('click',()=>document.getElementById('qr_image').click());document.getElementById('qr_image').addEventListener('change',event=>{readQRImage(event.target.files[0]);event.target.value=''});document.getElementById('import_qr').addEventListener('click',()=>importQRText(document.getElementById('qr_text').value));window.addEventListener('beforeunload',stopQR);
async function load(){try{const value=await api('/api/config');for(const key of ['base_url','device_id','android_user_id','poll_interval_seconds','watcher_idle_seconds','max_backoff_seconds','sms_database_path','mqtt_broker_url','mqtt_topic','mqtt_username'])document.getElementById(key).value=value[key]??'';document.getElementById('mqtt_password').value='';document.getElementById('transport_mode').value=value.transport_mode||'http';document.getElementById('fallback_to_http').checked=value.fallback_to_http!==false;for(const key of ['enabled','allow_insecure_local_http'])document.getElementById(key).checked=value[key];setStatus('已连接')}catch{setStatus('连接失败')}}
function showDatabase(value){const node=document.getElementById('database_diagnostic');node.textContent=value.valid?'数据库已验证：'+value.path+' · '+value.source+' · 最大ID '+value.max_id+' · 字段 '+(value.columns||[]).join(', '):'数据库未通过：'+(value.error_code||'未知错误')}
async function loadStatus(){try{const value=await api('/api/status');let label='服务运行中';if(value.state==='upload_failed'&&value.detail==='401')label='鉴权失败（401），请更新 HMAC 密钥';else if(value.state==='upload_failed')label='上传失败'+(value.detail?'（'+value.detail+'）':'');else if(value.state==='uploaded')label='最近上传成功'+(value.detail?'（'+value.detail+'）':'');else if(value.state==='quarantined')label='消息已隔离'+(value.detail?'（'+value.detail+'）':'');else if(value.state==='reader_failed')label='短信读取失败';else if(value.state==='not_configured')label='等待首次配置';else if(value.state==='config_invalid')label='配置无效';else if(value.state==='config_updated')label='配置已热生效';else if(value.state==='disabled')label='转发已停用';else if(value.state==='worker_stopped')label='服务未运行';else if(value.state==='unknown')label='暂无运行状态';serviceStatusNode.textContent=label;pendingCountNode.textContent=String(value.pending_count);document.getElementById('runtime_diagnostic').textContent='读取模式：'+(value.reader_mode||'等待扫描')+' · 数据库：'+(value.database_path||'自动检测中')+' · 来源：'+(value.database_source||'自动')+' · 游标：'+(value.database_cursor||0)+(value.database_error?' · 错误：'+value.database_error:'')}catch{serviceStatusNode.textContent='状态读取失败';pendingCountNode.textContent='--'}}
async function databaseRequest(path,body){try{const response=await fetch(path,{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});let result={valid:false,error_code:'database_request_failed'};try{result=await response.json()}catch{}showDatabase(result);return result}catch{showDatabase({valid:false,error_code:'database_request_failed'});return null}}
document.getElementById('validate_database').addEventListener('click',()=>databaseRequest('/api/database/validate',{path:document.getElementById('sms_database_path').value.trim(),android_user_id:Number(document.getElementById('android_user_id').value)}));
document.getElementById('redetect_database').addEventListener('click',async()=>{const result=await databaseRequest('/api/database/redetect',{android_user_id:Number(document.getElementById('android_user_id').value)});if(result&&result.valid)document.getElementById('sms_database_path').value=''});
document.getElementById('config').addEventListener('submit',async(event)=>{event.preventDefault();const save=document.getElementById('save');save.disabled=true;setStatus('正在保存');const payload={enabled:document.getElementById('enabled').checked,base_url:document.getElementById('base_url').value.trim(),allow_insecure_local_http:document.getElementById('allow_insecure_local_http').checked,transport_mode:document.getElementById('transport_mode').value,fallback_to_http:document.getElementById('fallback_to_http').checked,mqtt_broker_url:document.getElementById('mqtt_broker_url').value.trim(),mqtt_topic:document.getElementById('mqtt_topic').value.trim(),mqtt_username:document.getElementById('mqtt_username').value.trim(),mqtt_password:document.getElementById('mqtt_password').value,device_id:document.getElementById('device_id').value.trim(),android_user_id:Number(document.getElementById('android_user_id').value),poll_interval_seconds:Number(document.getElementById('poll_interval_seconds').value),watcher_idle_seconds:Number(document.getElementById('watcher_idle_seconds').value),max_backoff_seconds:Number(document.getElementById('max_backoff_seconds').value),sms_database_path:document.getElementById('sms_database_path').value.trim(),replace_hmac_secret:document.getElementById('replace_hmac_secret').checked,hmac_secret:document.getElementById('hmac_secret').value.trim()};try{const saved=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});document.getElementById('base_url').value=saved.base_url;document.getElementById('transport_mode').value=saved.transport_mode||'http';document.getElementById('fallback_to_http').checked=saved.fallback_to_http!==false;document.getElementById('mqtt_broker_url').value=saved.mqtt_broker_url||'';document.getElementById('mqtt_topic').value=saved.mqtt_topic||'';document.getElementById('mqtt_username').value=saved.mqtt_username||'';document.getElementById('sms_database_path').value=saved.sms_database_path||'';document.getElementById('hmac_secret').value='';document.getElementById('replace_hmac_secret').checked=false;setStatus('配置已热生效')}catch(error){setStatus(explainError(error))}finally{save.disabled=false}});
setInterval(loadStatus,3000);loadStatus();
setInterval(()=>api('/api/heartbeat',{method:'POST'}).catch(()=>setStatus('控制台已断开')),30000);load();
</script></body></html>`
