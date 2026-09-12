package main

import (
	"context"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/eclipse/paho.mqtt.golang"
	"nhooyr.io/websocket"
)

const (
	transportUploadTimeout          = 10 * time.Second
	transportRawBodyMaxBytes        = 16 * 1024
	transportWebSocketFrameMaxBytes = 32 * 1024
	mqttTopicTemplate               = "relay/v1/devices/{device_id}/messages"
)

var mqttTopicPattern = regexp.MustCompile(`^[^+#\s]+$`)

type transportEnvelope struct {
	SchemaVersion  string `json:"schema_version"`
	DeviceID       string `json:"device_id"`
	Timestamp      string `json:"timestamp"`
	Nonce          string `json:"nonce"`
	Signature      string `json:"signature"`
	IdempotencyKey string `json:"idempotency_key,omitempty"`
	PayloadB64     string `json:"payload_b64"`
}

type transportAck struct {
	Accepted   bool   `json:"accepted"`
	Duplicate  bool   `json:"duplicate"`
	StatusCode int    `json:"status_code"`
	MessageID  string `json:"message_id"`
	Code       string `json:"code"`
	Message    string `json:"message"`
}

type transportUploadOptions struct {
	configPath     string
	bodyPath       string
	timestamp      string
	nonce          string
	idempotencyKey string
	mode           string
}

func parseTransportUploadOptions(args []string) (transportUploadOptions, error) {
	options := transportUploadOptions{}
	for len(args) > 0 {
		if len(args) < 2 {
			return transportUploadOptions{}, fmt.Errorf("missing transport option value")
		}
		value := args[1]
		switch args[0] {
		case "--mode":
			options.mode = value
		case "--config":
			options.configPath = value
		case "--timestamp":
			options.timestamp = value
		case "--nonce":
			options.nonce = value
		case "--idempotency-key":
			options.idempotencyKey = value
		case "--body":
			options.bodyPath = value
		default:
			return transportUploadOptions{}, fmt.Errorf("unknown transport option")
		}
		args = args[2:]
	}
	if options.mode != "ws" && options.mode != "mqtt" || options.configPath == "" || options.bodyPath == "" ||
		options.timestamp == "" || options.nonce == "" || options.idempotencyKey == "" {
		return transportUploadOptions{}, fmt.Errorf("incomplete transport options")
	}
	return options, nil
}

func transportWebSocketURL(base string) (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(base))
	if err != nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", fmt.Errorf("invalid base URL")
	}
	switch parsed.Scheme {
	case "https":
		parsed.Scheme = "wss"
	case "http":
		parsed.Scheme = "ws"
	default:
		return "", fmt.Errorf("invalid base URL scheme")
	}
	parsed.Path = strings.TrimRight(parsed.Path, "/") + "/api/v1/ws/ingest"
	return parsed.String(), nil
}

func validateMQTTBrokerURL(raw string) error {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return fmt.Errorf("invalid MQTT broker URL")
	}
	if parsed.Port() != "" {
		port, portErr := strconv.Atoi(parsed.Port())
		if portErr != nil || port < 1 || port > 65535 {
			return fmt.Errorf("invalid MQTT broker URL port")
		}
	}
	switch parsed.Scheme {
	case "mqtt", "mqtts":
		if parsed.Path != "" && parsed.Path != "/" {
			return fmt.Errorf("MQTT TCP broker URL cannot contain a path")
		}
	case "ws", "wss":
	default:
		return fmt.Errorf("invalid MQTT broker URL scheme")
	}
	return nil
}

func validMQTTTopic(topic string) bool {
	return len(topic) <= 512 && mqttTopicPattern.MatchString(topic)
}

func validConfiguredMQTTTopic(topic, deviceID string) bool {
	if topic == "" || topic == mqttTopicTemplate {
		return true
	}
	if !validMQTTTopic(topic) || deviceID == "" {
		return false
	}
	return topic == "relay/v1/devices/"+deviceID+"/messages"
}

func resolvedMQTTTopic(config rootConfig) string {
	if config.MQTTTopic == "" {
		return "relay/v1/devices/" + config.DeviceID + "/messages"
	}
	return strings.ReplaceAll(config.MQTTTopic, "{device_id}", config.DeviceID)
}

func makeTransportEnvelope(config rootConfig, timestamp, nonce, idempotencyKey string, body []byte) (transportEnvelope, error) {
	secret, err := decodeSecret(config.HMACSecret)
	if err != nil {
		return transportEnvelope{}, err
	}
	return transportEnvelope{
		SchemaVersion:  "1",
		DeviceID:       config.DeviceID,
		Timestamp:      timestamp,
		Nonce:          nonce,
		Signature:      sign(secret, timestamp, nonce, body),
		IdempotencyKey: idempotencyKey,
		PayloadB64:     base64.RawURLEncoding.EncodeToString(body),
	}, nil
}

func marshalTransportFrame(envelope transportEnvelope) ([]byte, error) {
	frame, err := json.Marshal(struct {
		Type string `json:"type"`
		transportEnvelope
	}{Type: "message", transportEnvelope: envelope})
	if err != nil {
		return nil, err
	}
	if len(frame) > transportWebSocketFrameMaxBytes {
		return nil, fmt.Errorf("websocket frame too large")
	}
	return frame, nil
}

func uploadTransport(ctx context.Context, configPath, bodyPath, timestamp, nonce, idempotencyKey, mode string) (int, error) {
	config, err := loadConfig(configPath)
	if err != nil || !config.Enabled {
		return 0, fmt.Errorf("config invalid")
	}
	body, err := os.ReadFile(bodyPath)
	if err != nil || len(body) == 0 || len(body) > transportRawBodyMaxBytes {
		return 0, fmt.Errorf("body invalid")
	}
	envelope, err := makeTransportEnvelope(config, timestamp, nonce, idempotencyKey, body)
	if err != nil {
		return 0, err
	}
	switch mode {
	case "ws":
		return uploadWebSocket(ctx, config, envelope)
	case "mqtt":
		return uploadMQTT(ctx, config, envelope)
	default:
		return 0, fmt.Errorf("unsupported transport mode")
	}
}

func uploadWebSocket(parent context.Context, config rootConfig, envelope transportEnvelope) (int, error) {
	endpoint, err := transportWebSocketURL(config.BaseURL)
	if err != nil {
		return 0, err
	}
	ctx, cancel := context.WithTimeout(parent, transportUploadTimeout)
	defer cancel()
	connection, _, err := websocket.Dial(ctx, endpoint, &websocket.DialOptions{CompressionMode: websocket.CompressionDisabled})
	if err != nil {
		return 0, err
	}
	defer connection.Close(websocket.StatusNormalClosure, "upload complete")
	if _, _, err := connection.Read(ctx); err != nil {
		return 0, err
	}
	frame, err := marshalTransportFrame(envelope)
	if err != nil {
		return 0, err
	}
	if err := connection.Write(ctx, websocket.MessageText, frame); err != nil {
		return 0, err
	}
	_, rawAck, err := connection.Read(ctx)
	if err != nil {
		return 0, err
	}
	var ack transportAck
	if err := json.Unmarshal(rawAck, &ack); err != nil {
		return 0, err
	}
	if !ack.Accepted {
		return ack.StatusCode, fmt.Errorf("websocket ingest rejected: %s", ack.Message)
	}
	if ack.StatusCode == 0 {
		if ack.Duplicate {
			ack.StatusCode = 200
		} else {
			ack.StatusCode = 201
		}
	}
	return ack.StatusCode, nil
}

func uploadMQTT(parent context.Context, config rootConfig, envelope transportEnvelope) (int, error) {
	if err := validateMQTTBrokerURL(config.MQTTBrokerURL); err != nil {
		return 0, err
	}
	parsed, err := url.Parse(config.MQTTBrokerURL)
	if err != nil {
		return 0, err
	}
	broker := parsed.String()
	secure := parsed.Scheme == "mqtts" || parsed.Scheme == "wss"
	switch parsed.Scheme {
	case "mqtt":
		broker = "tcp://" + parsed.Host
	case "mqtts":
		broker = "ssl://" + parsed.Host
	}
	clientID := "sms-relay-root-" + strings.ReplaceAll(config.DeviceID, "-", "")
	options := mqtt.NewClientOptions().AddBroker(broker).SetClientID(clientID).SetCleanSession(true).SetKeepAlive(30 * time.Second).SetConnectTimeout(10 * time.Second).SetAutoReconnect(false)
	if config.MQTTUsername != "" {
		options.SetUsername(config.MQTTUsername)
		options.SetPassword(config.MQTTPassword)
	}
	if secure && (parsed.Scheme == "mqtts" || parsed.Scheme == "wss") {
		options.SetTLSConfig(&tls.Config{MinVersion: tls.VersionTLS12, ServerName: parsed.Hostname()})
	}
	client := mqtt.NewClient(options)
	ctx, cancel := context.WithTimeout(parent, transportUploadTimeout)
	defer cancel()
	connect := client.Connect()
	if !connect.WaitTimeout(10 * time.Second) {
		return 0, fmt.Errorf("MQTT connect timeout")
	}
	if err := connect.Error(); err != nil {
		return 0, err
	}
	defer client.Disconnect(250)
	payload, err := json.Marshal(envelope)
	if err != nil {
		return 0, err
	}
	token := client.Publish(resolvedMQTTTopic(config), 1, false, payload)
	if !token.WaitTimeout(transportUploadTimeout) {
		return 0, fmt.Errorf("MQTT publish timeout")
	}
	if err := token.Error(); err != nil {
		return 0, err
	}
	select {
	case <-ctx.Done():
		return 0, ctx.Err()
	default:
		return 201, nil
	}
}
