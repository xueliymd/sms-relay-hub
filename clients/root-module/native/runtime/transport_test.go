package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadConfigDefaultsTransportForLegacyConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	legacy := `{
  "schema_version": 2,
  "enabled": false,
  "base_url": "https://relay.example",
  "allow_insecure_local_http": false,
  "device_id": "",
  "hmac_secret": "",
  "source": "root_module",
  "android_user_id": 0,
  "initial_sync_mode": "new_only",
  "poll_interval_seconds": 3,
  "max_backoff_seconds": 300,
  "watcher_idle_seconds": 1,
  "sms_database_path": ""
}`
	if err := os.WriteFile(path, []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}
	config, err := loadConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if config.TransportMode != "http" || !config.FallbackToHTTP {
		t.Fatalf("legacy transport defaults=%q fallback=%v", config.TransportMode, config.FallbackToHTTP)
	}
}

func TestValidateConfigRejectsUnknownTransport(t *testing.T) {
	config := bootstrapRootConfig()
	config.TransportMode = "websocket"
	if err := validateConfig(config); err == nil {
		t.Fatal("unknown transport was accepted")
	}
}

func TestTransportWebSocketURL(t *testing.T) {
	tests := []struct {
		base string
		want string
	}{
		{base: "https://relay.example", want: "wss://relay.example/api/v1/ws/ingest"},
		{base: "http://127.0.0.1:39300", want: "ws://127.0.0.1:39300/api/v1/ws/ingest"},
	}
	for _, test := range tests {
		got, err := transportWebSocketURL(test.base)
		if err != nil {
			t.Fatalf("base=%q: %v", test.base, err)
		}
		if got != test.want {
			t.Fatalf("base=%q got=%q want=%q", test.base, got, test.want)
		}
	}
}

func TestValidateMQTTBrokerURL(t *testing.T) {
	valid := []string{
		"mqtt://broker.example:1883",
		"mqtts://broker.example:8883/",
		"ws://broker.example:8083/mqtt",
		"wss://broker.example:8084/mqtt/v1",
	}
	for _, raw := range valid {
		if err := validateMQTTBrokerURL(raw); err != nil {
			t.Fatalf("valid broker URL %q rejected: %v", raw, err)
		}
	}

	invalid := []string{
		"mqtt://broker.example/mqtt",
		"mqtts://broker.example/path",
		"wss://user:pass@broker.example/mqtt",
		"wss://broker.example/mqtt?token=secret",
		"wss://broker.example/mqtt#fragment",
		"wss:///mqtt",
		"https://broker.example:8883",
	}
	for _, raw := range invalid {
		if err := validateMQTTBrokerURL(raw); err == nil {
			t.Fatalf("invalid broker URL %q was accepted", raw)
		}
	}
}

func TestValidMQTTTopicUsesByteLimit(t *testing.T) {
	if !validMQTTTopic(strings.Repeat("a", 512)) {
		t.Fatal("512-byte topic was rejected")
	}
	if validMQTTTopic(strings.Repeat("a", 513)) {
		t.Fatal("513-byte topic was accepted")
	}
	if validMQTTTopic(strings.Repeat("中", 171)) {
		t.Fatal("topic over 512 bytes was accepted")
	}
	if !validMQTTTopic(strings.Repeat("中", 170)) {
		t.Fatal("topic at 510 bytes was rejected")
	}
}

func TestValidConfiguredMQTTTopicMatchesBackendContract(t *testing.T) {
	deviceID := "11111111-2222-4333-8444-555555555555"
	for _, topic := range []string{
		"",
		"relay/v1/devices/{device_id}/messages",
		"relay/v1/devices/11111111-2222-4333-8444-555555555555/messages",
	} {
		if !validConfiguredMQTTTopic(topic, deviceID) {
			t.Fatalf("backend-compatible topic %q was rejected", topic)
		}
	}
	for _, topic := range []string{
		"relay/messages",
		"relay/v1/devices/other/messages",
		"relay/v1/devices/{device_id}/other",
		"relay/v1/devices/11111111-2222-4333-8444-555555555555/messages/extra",
	} {
		if validConfiguredMQTTTopic(topic, deviceID) {
			t.Fatalf("unsupported MQTT topic %q was accepted", topic)
		}
	}
}

func TestTransportFrameFitsBackendLimitAtUploaderBodyLimit(t *testing.T) {
	config := rootConfig{
		HMACSecret: "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
		DeviceID:   "11111111-2222-4333-8444-555555555555",
	}
	envelope, err := makeTransportEnvelope(
		config,
		"1786883696",
		"transport-frame-test",
		"root:frame:1",
		bytes.Repeat([]byte("x"), 16*1024),
	)
	if err != nil {
		t.Fatal(err)
	}
	frame, err := marshalTransportFrame(envelope)
	if err != nil {
		t.Fatal(err)
	}
	if len(frame) > 32*1024 {
		t.Fatalf("frame bytes=%d exceed backend limit", len(frame))
	}
}

func TestTransportFrameRejectsOversizedEnvelope(t *testing.T) {
	_, err := marshalTransportFrame(transportEnvelope{PayloadB64: strings.Repeat("x", 32*1024)})
	if err == nil {
		t.Fatal("oversized websocket frame was accepted")
	}
}
