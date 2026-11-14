package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type commandTestExecutor struct {
	output []byte
	err    error
}

func (executor commandTestExecutor) Run(context.Context, string, ...string) ([]byte, error) {
	return executor.output, executor.err
}

type commandTestSMSReader struct{}

func (commandTestSMSReader) ListIDs(context.Context, int64, bool) ([]int64, error) {
	return []int64{1, 2, 3, 4, 5, 6}, nil
}

func (commandTestSMSReader) ReadField(_ context.Context, rowID int64, field string) ([]byte, error) {
	values := map[string]string{
		"address": "13800138000",
		"body":    "测试短信正文",
		"date":    "1760000000000",
	}
	return []byte("Row: 0 " + field + "=" + values[field]), nil
}

func TestDeviceCommandPendingSignsEmptyBody(t *testing.T) {
	secret := "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
	decoded, err := decodeSecret(secret)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet || request.URL.Path != "/api/v1/devices/00000000-0000-4000-8000-000000000001/commands/pending" {
			t.Errorf("unexpected request %s %s", request.Method, request.URL.Path)
		}
		body, readErr := io.ReadAll(request.Body)
		if readErr != nil {
			t.Error(readErr)
		}
		if len(body) != 0 {
			t.Errorf("GET pending body=%q", body)
		}
		timestamp := request.Header.Get("X-Timestamp")
		nonce := request.Header.Get("X-Nonce")
		if timestamp == "" || nonce == "" {
			t.Error("missing signed headers")
		}
		if got, want := request.Header.Get("X-Signature"), sign(decoded, timestamp, nonce, nil); got != want {
			t.Errorf("signature=%q want=%q", got, want)
		}
		response.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(response, `{"commands":[{"id":"cmd-1","command_type":"query_battery"}]}`)
	}))
	defer server.Close()

	client := newDeviceCommandClient(rootConfig{
		BaseURL:    server.URL,
		DeviceID:   "00000000-0000-4000-8000-000000000001",
		HMACSecret: secret,
	})
	commands, err := client.pending(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(commands) != 1 || commands[0].ID != "cmd-1" {
		t.Fatalf("commands=%+v", commands)
	}
}

func TestRootCommandsDefaultToNotAuthorized(t *testing.T) {
	for _, commandType := range []string{commandQueryBattery, commandQueryRecentSMS, commandTriggerUpload} {
		result := executeRootCommand(context.Background(), rootConfig{}, commandType)
		if result["error"] != "command_not_authorized" {
			t.Fatalf("%s result=%v", commandType, result)
		}
	}
}

func TestRootQueryBatteryParsesDumpsys(t *testing.T) {
	originalExecutor := newCommandExecutor
	t.Cleanup(func() { newCommandExecutor = originalExecutor })
	executor := &commandNameTestExecutor{output: []byte("level: 73\nstatus: 2\n")}
	newCommandExecutor = func() commandExecutor { return executor }
	result := executeRootCommand(context.Background(), rootConfig{AllowQueryBattery: true}, commandQueryBattery)
	if result["battery_percent"] != 73 || result["charging"] != true {
		t.Fatalf("result=%v", result)
	}
	if executor.name != "/system/bin/dumpsys" {
		t.Fatalf("dumpsys binary=%q", executor.name)
	}
}

func TestRootSMSReaderUsesAbsoluteContentPath(t *testing.T) {
	runner, ok := newCommandSMSReader(rootConfig{}).(contentProviderRunner)
	if !ok {
		t.Fatalf("reader type=%T", newCommandSMSReader(rootConfig{}))
	}
	if runner.binary != "/system/bin/content" {
		t.Fatalf("content binary=%q", runner.binary)
	}
}

func TestRootCommandSenderMaskingNeverExposesShortValues(t *testing.T) {
	for _, testCase := range []struct {
		input string
		want  string
	}{
		{input: "1234", want: "****"},
		{input: "123", want: "***"},
		{input: "A", want: "*"},
		{input: "13800138000", want: "*******8000"},
	} {
		if got := maskCommandSender(testCase.input); got != testCase.want {
			t.Fatalf("maskCommandSender(%q)=%q want=%q", testCase.input, got, testCase.want)
		}
	}
}

func TestRootQueryRecentSMSParsesAndMasks(t *testing.T) {
	originalReader := newCommandSMSReader
	t.Cleanup(func() { newCommandSMSReader = originalReader })
	newCommandSMSReader = func(rootConfig) commandSMSReader { return commandTestSMSReader{} }
	result := executeRootCommand(context.Background(), rootConfig{AllowQueryRecentSMS: true}, commandQueryRecentSMS)
	if result["count"] != 5 {
		t.Fatalf("result=%v", result)
	}
	messages, ok := result["messages"].([]map[string]any)
	if !ok || len(messages) != 5 {
		t.Fatalf("messages=%T %v", result["messages"], result["messages"])
	}
	if messages[0]["sender"] != "*******8000" {
		t.Fatalf("sender=%v", messages[0]["sender"])
	}
}

func TestRootTriggerUploadUsesWakeCommand(t *testing.T) {
	originalWake := triggerCommandWorker
	t.Cleanup(func() { triggerCommandWorker = originalWake })
	called := false
	triggerCommandWorker = func(context.Context) error {
		called = true
		return nil
	}
	result := executeRootCommand(context.Background(), rootConfig{AllowTriggerUpload: true}, commandTriggerUpload)
	if !called || result["triggered_at"] == nil {
		t.Fatalf("called=%v result=%v", called, result)
	}
}

func TestRootCommandWorkerContinuesAfterExpiredResult(t *testing.T) {
	secret := "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
	pendingCalls := 0
	resultCalls := 0
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		switch {
		case strings.HasSuffix(request.URL.Path, "/commands/pending"):
			pendingCalls++
			response.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(response, `{"commands":[{"id":"cmd-1","command_type":"query_battery"},{"id":"cmd-2","command_type":"query_battery"}]}`)
		case strings.HasSuffix(request.URL.Path, "/result"):
			resultCalls++
			if strings.Contains(request.URL.Path, "cmd-1") {
				response.WriteHeader(http.StatusConflict)
				_, _ = io.WriteString(response, `{"error":{"code":"command_expired"}}`)
				return
			}
			response.WriteHeader(http.StatusOK)
			_, _ = io.WriteString(response, `{"status":"completed"}`)
		default:
			t.Errorf("unexpected path %s", request.URL.Path)
		}
	}))
	defer server.Close()

	configPath := filepath.Join(t.TempDir(), "config.json")
	config := rootConfig{
		SchemaVersion:          2,
		Enabled:                true,
		BaseURL:                server.URL,
		AllowInsecureLocalHTTP: true,
		TransportMode:          "http",
		DeviceID:               "00000000-0000-4000-8000-000000000001",
		HMACSecret:             secret,
		Source:                 "root_module",
		InitialSyncMode:        "new_only",
		PollIntervalSeconds:    3,
		MaxBackoffSeconds:      300,
		AllowQueryBattery:      true,
	}
	if err := writeConfigAtomic(configPath, config); err != nil {
		t.Fatal(err)
	}
	originalExecutor := newCommandExecutor
	t.Cleanup(func() { newCommandExecutor = originalExecutor })
	newCommandExecutor = func() commandExecutor {
		return commandTestExecutor{output: []byte("level: 50\nstatus: 3\n")}
	}

	if err := runDeviceCommands(context.Background(), configPath); err != nil {
		t.Fatalf("expired result should not abort the batch: %v", err)
	}
	if pendingCalls != 1 || resultCalls != 2 {
		t.Fatalf("pending=%d result=%d", pendingCalls, resultCalls)
	}
}

func TestRootCommandWorkerStopsOnUnexpectedConflict(t *testing.T) {
	secret := "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
	resultCalls := 0
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		switch {
		case strings.HasSuffix(request.URL.Path, "/commands/pending"):
			response.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(response, `{"commands":[{"id":"cmd-conflict","command_type":"query_battery"}]}`)
		case strings.HasSuffix(request.URL.Path, "/result"):
			resultCalls++
			response.WriteHeader(http.StatusConflict)
			_, _ = io.WriteString(response, `{"error":{"code":"command_conflict"}}`)
		default:
			t.Errorf("unexpected path %s", request.URL.Path)
		}
	}))
	defer server.Close()

	configPath := filepath.Join(t.TempDir(), "config.json")
	config := rootConfig{
		SchemaVersion:          2,
		Enabled:                true,
		BaseURL:                server.URL,
		AllowInsecureLocalHTTP: true,
		TransportMode:          "http",
		DeviceID:               "00000000-0000-4000-8000-000000000001",
		HMACSecret:             secret,
		Source:                 "root_module",
		InitialSyncMode:        "new_only",
		PollIntervalSeconds:    3,
		MaxBackoffSeconds:      300,
		AllowQueryBattery:      true,
	}
	if err := writeConfigAtomic(configPath, config); err != nil {
		t.Fatal(err)
	}
	originalExecutor := newCommandExecutor
	t.Cleanup(func() { newCommandExecutor = originalExecutor })
	newCommandExecutor = func() commandExecutor {
		return commandTestExecutor{output: []byte("level: 50\nstatus: 3\n")}
	}

	err := runDeviceCommands(context.Background(), configPath)
	if err == nil || err.Error() != "command_http_409" {
		t.Fatalf("unexpected error: %v", err)
	}
	if resultCalls != 1 {
		t.Fatalf("result calls=%d", resultCalls)
	}
}

func TestRootCommandAuthorizationPersistsThroughWebConsole(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	config := rootConfig{
		SchemaVersion:       2,
		Enabled:             true,
		BaseURL:             "https://relay.example",
		TransportMode:       "http",
		DeviceID:            "00000000-0000-4000-8000-000000000001",
		HMACSecret:          "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
		Source:              "root_module",
		InitialSyncMode:     "new_only",
		PollIntervalSeconds: 3,
		MaxBackoffSeconds:   300,
		WatcherIdleSeconds:  1,
	}
	if err := writeConfigAtomic(configPath, config); err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(map[string]any{
		"enabled": true, "base_url": config.BaseURL, "allow_insecure_local_http": false,
		"transport_mode": "http", "fallback_to_http": true, "mqtt_broker_url": "",
		"mqtt_topic": "", "mqtt_username": "", "mqtt_password": "", "device_id": config.DeviceID,
		"android_user_id": 0, "poll_interval_seconds": 3, "watcher_idle_seconds": 1,
		"max_backoff_seconds": 300, "sms_database_path": "", "replace_hmac_secret": false,
		"hmac_secret": "", "allow_query_battery": true, "allow_query_recent_sms": true,
		"allow_trigger_upload": true,
	})
	if err != nil {
		t.Fatal(err)
	}
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	updated, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if !updated.AllowQueryBattery || !updated.AllowQueryRecentSMS || !updated.AllowTriggerUpload {
		t.Fatalf("flags were not persisted: %+v", updated)
	}
	for _, id := range []string{"allow_query_battery", "allow_query_recent_sms", "allow_trigger_upload"} {
		if !strings.Contains(webConsoleHTML, id) {
			t.Fatalf("console control missing: %s", id)
		}
	}
	_ = os.Remove(filepath.Join(stateDir, "run", "config.changed"))
}

type commandNameTestExecutor struct {
	name   string
	output []byte
	err    error
}

func (executor *commandNameTestExecutor) Run(_ context.Context, name string, _ ...string) ([]byte, error) {
	executor.name = name
	return executor.output, executor.err
}

func TestWakeCommandWorkerUsesConfiguredHelper(t *testing.T) {
	helper := filepath.Join(t.TempDir(), "sms-relay-wake")
	t.Setenv("SMS_RELAY_WAKE_HELPER", helper)
	executor := &commandNameTestExecutor{}
	if err := wakeCommandWorker(context.Background(), executor); err != nil {
		t.Fatal(err)
	}
	if executor.name != helper {
		t.Fatalf("wake helper=%q want=%q", executor.name, helper)
	}
}

func TestWakeCommandWorkerDefaultsToInstalledHelper(t *testing.T) {
	t.Setenv("SMS_RELAY_WAKE_HELPER", "")
	executor := &commandNameTestExecutor{}
	if err := wakeCommandWorker(context.Background(), executor); err != nil {
		t.Fatal(err)
	}
	if executor.name != "/system/bin/sms-relay-wake" {
		t.Fatalf("wake helper=%q", executor.name)
	}
}
