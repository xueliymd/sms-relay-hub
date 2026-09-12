package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func writeWebConsoleTestConfig(t *testing.T, path string) rootConfig {
	t.Helper()
	config := rootConfig{
		SchemaVersion:          2,
		Enabled:                true,
		BaseURL:                "https://relay.example",
		AllowInsecureLocalHTTP: false,
		DeviceID:               "00000000-0000-4000-8000-000000000001",
		HMACSecret:             "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
		Source:                 "root_module",
		AndroidUserID:          0,
		InitialSyncMode:        "new_only",
		PollIntervalSeconds:    15,
		MaxBackoffSeconds:      300,
	}
	if err := writeConfigAtomic(path, config); err != nil {
		t.Fatal(err)
	}
	return config
}

func authorizedConsoleRequest(method, target, token string, body []byte) *http.Request {
	request := httptest.NewRequest(method, target, bytes.NewReader(body))
	request.Host = "127.0.0.1:35000"
	request.Header.Set("X-SMS-Relay-Token", token)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	return request
}

func TestWebConsoleConfigIsAuthenticatedAndRedacted(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	writeWebConsoleTestConfig(t, configPath)
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")

	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/api/config", nil))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("unauthorized status=%d", unauthorized.Code)
	}

	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodGet, "/api/config", "session-token", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if strings.Contains(response.Body.String(), "AAECAwQF") {
		t.Fatalf("secret leaked: %s", response.Body.String())
	}
	var rawView map[string]json.RawMessage
	if err := json.Unmarshal(response.Body.Bytes(), &rawView); err != nil {
		t.Fatal(err)
	}
	if _, exists := rawView["hmac_secret"]; exists {
		t.Fatalf("secret field leaked: %s", response.Body.String())
	}
	var view webConfigView
	if err := json.Unmarshal(response.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if !view.HasHMACSecret || view.PollIntervalSeconds != 15 || view.WatcherIdleSeconds != 1 {
		t.Fatalf("view=%+v", view)
	}
}

func TestParseEnrollmentQRAcceptsRootEnrollmentPayload(t *testing.T) {
	qr := `{"v":1,"type":"sms-relay-enroll","url":"https://relay.example","device_id":"00000000-0000-4000-8000-000000000001","token":"0123456789abcdefghijklmnopqrstuvwxyzABCD","expires_at":"2099-01-01T00:00:00Z"}`
	payload, err := parseEnrollmentQR(qr, time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if payload.URL != "https://relay.example" || payload.DeviceID != "00000000-0000-4000-8000-000000000001" {
		t.Fatalf("payload=%+v", payload)
	}
}

func TestParseEnrollmentQRRejectsExpiredAndUnknownPayloads(t *testing.T) {
	now := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	expired := "{\"v\":1,\"type\":\"sms-relay-enroll\",\"url\":\"https://relay.example\",\"device_id\":\"00000000-0000-4000-8000-000000000001\",\"token\":\"0123456789abcdefghijklmnopqrstuvwxyzABCD\",\"expires_at\":\"2026-09-02T23:59:59Z\"}"
	if _, err := parseEnrollmentQR(expired, now); err == nil {
		t.Fatal("expired QR was accepted")
	}
	unknown := "{\"v\":1,\"type\":\"sms-relay-enroll\",\"url\":\"https://relay.example\",\"device_id\":\"00000000-0000-4000-8000-000000000001\",\"token\":\"0123456789abcdefghijklmnopqrstuvwxyzABCD\",\"expires_at\":\"2099-01-01T00:00:00Z\",\"secret\":\"leak\"}"
	if _, err := parseEnrollmentQR(unknown, now); err == nil {
		t.Fatal("unknown QR field was accepted")
	}
}

func TestExchangeEnrollmentAcceptsMatchingRootCredential(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.URL.Path != "/api/v1/enrollment/consume" {
			t.Fatalf("request=%s %s", request.Method, request.URL.Path)
		}
		var body map[string]string
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		if body["device_id"] == "" || body["enrollment_token"] == "" {
			t.Fatalf("body=%v", body)
		}
		response.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(response).Encode(enrollmentCredential{
			DeviceID: "00000000-0000-4000-8000-000000000001", ClientType: "root_module", AuthKind: "hmac",
			HMACSecret: "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
		})
	}))
	defer server.Close()
	credential, err := exchangeEnrollment(context.Background(), enrollmentQRPayload{
		URL: server.URL, DeviceID: "00000000-0000-4000-8000-000000000001", Token: "0123456789abcdefghijklmnopqrstuvwxyzABCD",
	})
	if err != nil {
		t.Fatal(err)
	}
	if credential.ClientType != "root_module" || credential.AuthKind != "hmac" || credential.HMACSecret == "" {
		t.Fatalf("credential=%+v", credential)
	}
}

func TestWebConsoleHTMLIncludesQREnrollmentFallbacks(t *testing.T) {
	for _, fragment := range []string{
		"快速配置",
		"上报通道",
		"高级诊断",
		"id=\"panel-quick\"",
		"id=\"panel-transport\"",
		"id=\"panel-advanced\"",
		"class=\"console-top\"",
		"class=\"status-card\"",
		"id=\"pending_count\"",
		"class=\"console-card\"",
		"overflow-x:auto",
		"id=\"scan_qr\"",
		"id=\"choose_qr_image\"",
		"id=\"qr_text\"",
		"/api/enrollment",
		"BarcodeDetector",
	} {
		if !strings.Contains(webConsoleHTML, fragment) {
			t.Fatalf("web console is missing %q", fragment)
		}
	}
}

func TestWebConsoleEnrollmentImportsRootCredentialAndSignalsReload(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	handler.enrollmentClient = func(_ context.Context, payload enrollmentQRPayload) (enrollmentCredential, error) {
		if payload.DeviceID != "00000000-0000-4000-8000-000000000001" {
			t.Fatalf("device id=%s", payload.DeviceID)
		}
		return enrollmentCredential{
			DeviceID: payload.DeviceID, ClientType: "root_module", AuthKind: "hmac",
			HMACSecret: "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
		}, nil
	}
	qr := "{\"v\":1,\"type\":\"sms-relay-enroll\",\"url\":\"https://relay.example\",\"device_id\":\"00000000-0000-4000-8000-000000000001\",\"token\":\"0123456789abcdefghijklmnopqrstuvwxyzABCD\",\"expires_at\":\"2099-01-01T00:00:00Z\"}"
	body, err := json.Marshal(map[string]string{"qr_text": qr})
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/enrollment", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if saved.BaseURL != "https://relay.example" || saved.DeviceID != "00000000-0000-4000-8000-000000000001" || saved.HMACSecret == "" {
		t.Fatalf("saved=%+v", saved)
	}
	if _, err := os.Stat(filepath.Join(stateDir, "run", "config.changed")); err != nil {
		t.Fatal("reload marker missing")
	}
}

func TestWebConsoleMissingConfigUsesBootstrapDefaults(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")

	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodGet, "/api/config", "session-token", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	var view webConfigView
	if err := json.Unmarshal(response.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if view.Enabled || view.HasHMACSecret || view.BaseURL != "" || view.DeviceID != "" ||
		view.AndroidUserID != 0 || view.PollIntervalSeconds != 3 || view.WatcherIdleSeconds != 1 || view.MaxBackoffSeconds != 300 {
		t.Fatalf("view=%+v", view)
	}
	if _, err := os.Stat(configPath); !os.IsNotExist(err) {
		t.Fatalf("bootstrap GET created config: %v", err)
	}
}

func TestWebConsoleCreatesFirstConfigAndSignalsReload(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	update := webConfigUpdate{
		Enabled:                true,
		BaseURL:                "http://192.168.2.109:8000",
		AllowInsecureLocalHTTP: true,
		DeviceID:               "00000000-0000-4000-8000-000000000001",
		AndroidUserID:          0,
		PollIntervalSeconds:    3,
		MaxBackoffSeconds:      300,
		ReplaceHMACSecret:      true,
		HMACSecret:             "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
	}
	body, err := json.Marshal(update)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if !saved.Enabled || saved.SchemaVersion != 2 || saved.Source != "root_module" ||
		saved.InitialSyncMode != "new_only" || saved.HMACSecret != update.HMACSecret {
		t.Fatalf("saved=%+v", saved)
	}
	info, err := os.Stat(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("config mode=%o", info.Mode().Perm())
	}
	if _, err := os.Stat(filepath.Join(stateDir, "run", "config.changed")); err != nil {
		t.Fatal("reload marker missing")
	}
}

func TestWebConsoleNormalizesLocalEndpointWithoutScheme(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	update := webConfigUpdate{
		Enabled:                true,
		BaseURL:                "192.168.2.109:8000",
		AllowInsecureLocalHTTP: true,
		DeviceID:               "00000000-0000-4000-8000-000000000001",
		PollIntervalSeconds:    3,
		MaxBackoffSeconds:      300,
		ReplaceHMACSecret:      true,
		HMACSecret:             "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
	}
	body, err := json.Marshal(update)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if saved.BaseURL != "http://192.168.2.109:8000" {
		t.Fatalf("base URL was not normalized: %q", saved.BaseURL)
	}
}

func TestWebConsoleDoesNotBootstrapOverInvalidConfig(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	if err := os.WriteFile(configPath, []byte("{bad"), 0o600); err != nil {
		t.Fatal(err)
	}
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodGet, "/api/config", "session-token", nil))
	if response.Code != http.StatusInternalServerError {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
}

func TestWebConsoleStatusIsAuthenticatedAndCountsPending(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	writeWebConsoleTestConfig(t, configPath)
	if err := os.MkdirAll(filepath.Join(stateDir, "spool"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(stateDir, "run", "inflight", "claim.1"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stateDir, "spool", "queued.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stateDir, "run", "inflight", "claim.1", "retry.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	status := `{"state":"upload_failed","detail":"401","updated_at":"2026-08-18T04:00:00Z"}` + "\n"
	if err := os.WriteFile(filepath.Join(stateDir, "run", "status.json"), []byte(status), 0o600); err != nil {
		t.Fatal(err)
	}
	writeReaderDiagnostic(filepath.Join(stateDir, "reader"), readerDiagnostic{
		Mode: "SQLite", Path: "/data/user/0/com.android.providers.telephony/databases/mmssms.db",
		Source: "自动", MaxID: 15641, Cursor: 15641, Columns: []string{"_id", "sim_id"},
	})
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	handler.workerRunning = func() bool { return true }

	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/api/status", nil))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("unauthorized status=%d", unauthorized.Code)
	}

	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodGet, "/api/status", "session-token", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	var view webStatusView
	if err := json.Unmarshal(response.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if view.State != "upload_failed" || view.Detail != "401" || view.PendingCount != 2 || view.UpdatedAt == "" ||
		view.ReaderMode != "SQLite" || view.DatabaseCursor != 15641 || view.DatabaseMaxID != 15641 {
		t.Fatalf("view=%+v", view)
	}
}

func TestWebConsoleStatusDoesNotReportStaleUploadWhenWorkerStopped(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	writeWebConsoleTestConfig(t, configPath)
	if err := os.MkdirAll(filepath.Join(stateDir, "run"), 0o700); err != nil {
		t.Fatal(err)
	}
	status := `{"state":"uploaded","detail":"201","updated_at":"2026-08-18T04:00:00Z"}` + "\n"
	if err := os.WriteFile(filepath.Join(stateDir, "run", "status.json"), []byte(status), 0o600); err != nil {
		t.Fatal(err)
	}
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	handler.workerRunning = func() bool { return false }

	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodGet, "/api/status", "session-token", nil))
	var view webStatusView
	if err := json.Unmarshal(response.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if view.State != "worker_stopped" || view.Detail != "" {
		t.Fatalf("view=%+v", view)
	}
}

func TestWebConsoleStatusReportsMissingFirstConfigBeforeStoppedWorker(t *testing.T) {
	stateDir := t.TempDir()
	handler := newWebConsoleHandler(filepath.Join(stateDir, "config.json"), stateDir, "session-token", "127.0.0.1:35000")
	handler.workerRunning = func() bool { return false }
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodGet, "/api/status", "session-token", nil))
	var view webStatusView
	if err := json.Unmarshal(response.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	if view.State != "not_configured" {
		t.Fatalf("view=%+v", view)
	}
}

func TestWebConsoleEnabledSaveClearsDisabledMarkerAndStartsStoppedWorker(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	original := writeWebConsoleTestConfig(t, configPath)
	if err := os.WriteFile(filepath.Join(stateDir, "disabled"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	handler.workerRunning = func() bool { return false }
	starts := 0
	handler.startService = func() error { starts++; return nil }
	update := webConfigUpdate{
		Enabled:             true,
		BaseURL:             original.BaseURL,
		DeviceID:            original.DeviceID,
		AndroidUserID:       original.AndroidUserID,
		PollIntervalSeconds: original.PollIntervalSeconds,
		MaxBackoffSeconds:   original.MaxBackoffSeconds,
	}
	body, err := json.Marshal(update)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if starts != 1 {
		t.Fatalf("service starts=%d", starts)
	}
	if _, err := os.Stat(filepath.Join(stateDir, "disabled")); !os.IsNotExist(err) {
		t.Fatalf("disabled marker retained: %v", err)
	}
}

func TestWebConsoleDisabledSaveWritesStopMarker(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	original := writeWebConsoleTestConfig(t, configPath)
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	update := webConfigUpdate{
		Enabled:             false,
		BaseURL:             original.BaseURL,
		DeviceID:            original.DeviceID,
		AndroidUserID:       original.AndroidUserID,
		PollIntervalSeconds: original.PollIntervalSeconds,
		MaxBackoffSeconds:   original.MaxBackoffSeconds,
	}
	body, err := json.Marshal(update)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if info, err := os.Stat(filepath.Join(stateDir, "disabled")); err != nil || !info.Mode().IsRegular() {
		t.Fatalf("disabled marker missing: %v", err)
	}
}

func TestWebConsolePagePollsRuntimeStatus(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	writeWebConsoleTestConfig(t, configPath)
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.Host = "127.0.0.1:35000"
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d", response.Code)
	}
	page := response.Body.String()
	for _, expected := range []string{"service_status", "/api/status", "高级设置（机型兼容与延迟排查）", "sms_database_path", "验证数据库", "重新自动检测", "await response.json", "只影响断网或服务器失败重试", "setInterval(loadStatus,3000)", "document.getElementById('base_url').value=saved.base_url", "worker_stopped')label='服务未运行", "credential_confirmation_required", "保存失败：", "扫码导入后台设备配置", "transport_mode", "fallback_to_http", "mqtt_broker_url"} {
		if !strings.Contains(page, expected) {
			t.Fatalf("page missing %q", expected)
		}
	}
	if strings.Contains(page, "session-token") {
		t.Fatal("session token leaked into HTML")
	}
}

func TestWebConsoleAcceptsTrimmedPaddedHMACSecretOnFirstSave(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	update := webConfigUpdate{
		Enabled: true, BaseURL: "https://relay.example.com", DeviceID: "11111111-2222-4333-8444-555555555555",
		AndroidUserID: 0, PollIntervalSeconds: 3, WatcherIdleSeconds: 1, MaxBackoffSeconds: 300,
		ReplaceHMACSecret: true, HMACSecret: " AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8== ",
	}
	body, err := json.Marshal(update)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if saved.HMACSecret != "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8" {
		t.Fatalf("secret was not normalized: %q", saved.HMACSecret)
	}
}

func TestWebConsoleValidatesManualDatabaseBeforeSaving(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	original := writeWebConsoleTestConfig(t, configPath)
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	handler.databaseProbe = func(_ context.Context, userID int, path string) webDatabaseView {
		if userID == 0 && path == "/data/user/0/com.android.providers.telephony/databases/mmssms.db" {
			return webDatabaseView{Valid: true, Path: path, Source: "manual", MaxID: 15641, Columns: []string{"_id", "sim_id"}}
		}
		return webDatabaseView{ErrorCode: "database_not_found"}
	}
	update := webConfigUpdate{
		Enabled: true, BaseURL: original.BaseURL, DeviceID: original.DeviceID,
		AndroidUserID: 0, PollIntervalSeconds: 3, WatcherIdleSeconds: 1, MaxBackoffSeconds: 300,
		SMSDatabasePath: "/data/user/0/com.android.providers.telephony/databases/mmssms.db",
	}
	body, err := json.Marshal(update)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if saved.SMSDatabasePath != update.SMSDatabasePath {
		t.Fatalf("database path=%q", saved.SMSDatabasePath)
	}
}

func TestWebConsoleRejectsInvalidManualDatabaseWithoutChangingConfig(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	original := writeWebConsoleTestConfig(t, configPath)
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	handler.databaseProbe = func(context.Context, int, string) webDatabaseView {
		return webDatabaseView{ErrorCode: "database_not_found"}
	}
	update := webConfigUpdate{
		Enabled: true, BaseURL: original.BaseURL, DeviceID: original.DeviceID,
		AndroidUserID: 0, PollIntervalSeconds: 3, WatcherIdleSeconds: 1, MaxBackoffSeconds: 300,
		SMSDatabasePath: "/data/user/0/com.android.providers.telephony/databases/mmssms.db",
	}
	body, _ := json.Marshal(update)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if saved.SMSDatabasePath != original.SMSDatabasePath {
		t.Fatal("invalid database changed config")
	}
}

func TestWebConsoleRedetectClearsManualPathOnlyAfterSuccess(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	config := writeWebConsoleTestConfig(t, configPath)
	config.SMSDatabasePath = "/data/user/0/com.android.providers.telephony/databases/mmssms.db"
	if err := writeConfigAtomic(configPath, config); err != nil {
		t.Fatal(err)
	}
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	handler.databaseProbe = func(context.Context, int, string) webDatabaseView {
		return webDatabaseView{Valid: true, Path: "/data/user/0/com.android.providers.telephony/databases/mmssms.db", Source: "automatic", MaxID: 15641}
	}
	body := []byte(`{"android_user_id":0,"path":""}`)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/database/redetect", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if saved.SMSDatabasePath != "" {
		t.Fatalf("manual path retained: %q", saved.SMSDatabasePath)
	}
}

func TestWebConsoleRedetectDoesNotCreateIncompleteConfig(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	handler.databaseProbe = func(context.Context, int, string) webDatabaseView {
		return webDatabaseView{Valid: true, Path: "/data/user/0/com.android.providers.telephony/databases/mmssms.db", Source: "automatic"}
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/database/redetect", "session-token", []byte(`{"android_user_id":0,"path":""}`)))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if _, err := os.Stat(configPath); !os.IsNotExist(err) {
		t.Fatalf("redetect created config: %v", err)
	}
}

func TestWebConsoleUpdatesConfigAtomicallyAndSignalsReload(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	original := writeWebConsoleTestConfig(t, configPath)
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	update := webConfigUpdate{
		Enabled:                true,
		BaseURL:                "http://192.168.2.109:8000",
		AllowInsecureLocalHTTP: true,
		DeviceID:               original.DeviceID,
		AndroidUserID:          1,
		PollIntervalSeconds:    3,
		WatcherIdleSeconds:     7,
		MaxBackoffSeconds:      60,
	}
	body, err := json.Marshal(update)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}

	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if saved.HMACSecret != original.HMACSecret || saved.BaseURL != update.BaseURL || saved.AndroidUserID != 1 || saved.PollIntervalSeconds != 3 || saved.WatcherIdleSeconds != 7 {
		t.Fatalf("saved=%+v", saved)
	}
	info, err := os.Stat(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("config mode=%o", info.Mode().Perm())
	}
	if _, err := os.Stat(filepath.Join(stateDir, "run", "config.changed")); err != nil {
		t.Fatal("reload marker missing")
	}
}

func TestWebConsoleRejectsInvalidSecretReplacementWithoutChangingConfig(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	original := writeWebConsoleTestConfig(t, configPath)
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	update := webConfigUpdate{
		Enabled:             true,
		BaseURL:             original.BaseURL,
		DeviceID:            original.DeviceID,
		AndroidUserID:       0,
		PollIntervalSeconds: 3,
		MaxBackoffSeconds:   300,
		ReplaceHMACSecret:   true,
		HMACSecret:          "invalid",
	}
	body, err := json.Marshal(update)
	if err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, authorizedConsoleRequest(http.MethodPost, "/api/config", "session-token", body))
	if response.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status=%d", response.Code)
	}
	saved, err := loadConfig(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if saved.HMACSecret != original.HMACSecret || saved.PollIntervalSeconds != original.PollIntervalSeconds {
		t.Fatal("invalid update changed the config")
	}
}

func TestWebConsoleListenAddressIsLoopbackPort35000Only(t *testing.T) {
	if err := validateWebListenAddress("127.0.0.1:35000"); err != nil {
		t.Fatal(err)
	}
	for _, address := range []string{"0.0.0.0:35000", "127.0.0.1:8080", "[::1]:35000", "localhost:35000"} {
		if err := validateWebListenAddress(address); err == nil {
			t.Fatalf("accepted address %q", address)
		}
	}
}

func TestWebConsoleServerStopsAfterIdleTimeout(t *testing.T) {
	stateDir := t.TempDir()
	configPath := filepath.Join(stateDir, "config.json")
	writeWebConsoleTestConfig(t, configPath)
	handler := newWebConsoleHandler(configPath, stateDir, "session-token", "127.0.0.1:35000")
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		done <- serveWebConsole(listener, handler, 80*time.Millisecond)
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("web console did not stop after idle timeout")
	}
}
