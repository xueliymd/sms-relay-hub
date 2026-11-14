package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	crand "crypto/rand"
)

const (
	commandQueryBattery   = "query_battery"
	commandQueryRecentSMS = "query_recent_sms"
	commandTriggerUpload  = "trigger_upload"
	commandHTTPTimeout    = 10 * time.Second
	commandBodyLimit      = 64 * 1024
	recentSMSLimit        = 5
)

type pendingDeviceCommand struct {
	ID          string `json:"id"`
	CommandType string `json:"command_type"`
}

type pendingCommandResponse struct {
	Commands []pendingDeviceCommand `json:"commands"`
}

type deviceCommandClient struct {
	config rootConfig
	http   *http.Client
}

type commandSMSReader interface {
	ListIDs(context.Context, int64, bool) ([]int64, error)
	ReadField(context.Context, int64, string) ([]byte, error)
}

// Keep the hardware-facing dependencies swappable so command behavior can be
// tested without a rooted Android device. Production always uses the real
// executor and content provider runner below.
var (
	newCommandExecutor  = func() commandExecutor { return execCommandExecutor{} }
	newCommandSMSReader = func(config rootConfig) commandSMSReader {
		return contentProviderRunner{
			executor: execCommandExecutor{},
			binary:   "/system/bin/content",
			userID:   config.AndroidUserID,
		}
	}
	triggerCommandWorker = func(ctx context.Context) error {
		return wakeCommandWorker(ctx, execCommandExecutor{})
	}
)

func wakeCommandWorker(ctx context.Context, executor commandExecutor) error {
	helper := strings.TrimSpace(os.Getenv("SMS_RELAY_WAKE_HELPER"))
	if helper == "" {
		helper = "/system/bin/sms-relay-wake"
	}
	_, err := executor.Run(ctx, helper)
	return err
}

func newDeviceCommandClient(config rootConfig) *deviceCommandClient {
	return &deviceCommandClient{
		config: config,
		http: &http.Client{
			Timeout: commandHTTPTimeout,
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12},
			},
		},
	}
}

func (client *deviceCommandClient) pending(ctx context.Context) ([]pendingDeviceCommand, error) {
	body, _, err := client.request(ctx, http.MethodGet, "/api/v1/devices/"+client.config.DeviceID+"/commands/pending", nil)
	if err != nil {
		return nil, err
	}
	var payload pendingCommandResponse
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, fmt.Errorf("invalid command response")
	}
	return payload.Commands, nil
}

func (client *deviceCommandClient) postResult(ctx context.Context, commandID string, result map[string]any) (int, string, error) {
	payload, err := json.Marshal(map[string]any{"result": result})
	if err != nil {
		return 0, "", err
	}
	body, status, err := client.request(ctx, http.MethodPost, "/api/v1/devices/"+client.config.DeviceID+"/commands/"+commandID+"/result", payload)
	return status, commandErrorCode(body), err
}

func commandErrorCode(body []byte) string {
	var payload struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return ""
	}
	return strings.TrimSpace(payload.Error.Code)
}

func (client *deviceCommandClient) request(ctx context.Context, method, path string, body []byte) ([]byte, int, error) {
	secret, err := decodeSecret(client.config.HMACSecret)
	if err != nil {
		return nil, 0, err
	}
	timestamp := strconv.FormatInt(time.Now().Unix(), 10)
	nonce, err := newNonce(crand.Reader)
	if err != nil {
		return nil, 0, err
	}
	request, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(client.config.BaseURL, "/")+path, strings.NewReader(string(body)))
	if err != nil {
		return nil, 0, err
	}
	request.Header.Set("X-Device-Id", client.config.DeviceID)
	request.Header.Set("X-Timestamp", timestamp)
	request.Header.Set("X-Nonce", nonce)
	request.Header.Set("X-Signature", sign(secret, timestamp, nonce, body))
	if len(body) > 0 {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := client.http.Do(request)
	if err != nil {
		return nil, 0, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, commandBodyLimit))
	if err != nil {
		return nil, response.StatusCode, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return raw, response.StatusCode, fmt.Errorf("command_http_%d", response.StatusCode)
	}
	return raw, response.StatusCode, nil
}

func runDeviceCommands(ctx context.Context, configPath string) error {
	config, err := loadConfig(configPath)
	if err != nil || !config.Enabled {
		return fmt.Errorf("config_invalid")
	}
	client := newDeviceCommandClient(config)
	commands, err := client.pending(ctx)
	if err != nil {
		return err
	}
	for _, command := range commands {
		result := executeRootCommand(ctx, config, command.CommandType)
		status, code, err := client.postResult(ctx, command.ID, result)
		if err != nil {
			// 单条指令已过期不应阻塞同一批次的后续指令。其他错误仍交给
			// 服务循环按退避策略重试，避免网络故障时连续无效请求。
			if status == http.StatusConflict && code == "command_expired" {
				continue
			}
			return err
		}
	}
	return nil
}

func executeRootCommand(ctx context.Context, config rootConfig, commandType string) map[string]any {
	switch commandType {
	case commandQueryBattery:
		if !config.AllowQueryBattery {
			return commandNotAuthorized()
		}
		return queryRootBattery(ctx)
	case commandQueryRecentSMS:
		if !config.AllowQueryRecentSMS {
			return commandNotAuthorized()
		}
		return queryRootRecentSMS(ctx, config)
	case commandTriggerUpload:
		if !config.AllowTriggerUpload {
			return commandNotAuthorized()
		}
		return triggerRootUpload(ctx)
	default:
		return map[string]any{
			"error":   "unsupported_command",
			"message": "当前 Root 模块版本不支持该指令",
		}
	}
}

func commandNotAuthorized() map[string]any {
	return map[string]any{
		"error":   "command_not_authorized",
		"message": "Root 模块未开启该指令授权，请在模块控制台开启后重试",
	}
}

func queryRootBattery(ctx context.Context) map[string]any {
	executor := newCommandExecutor()
	raw, err := executor.Run(ctx, "/system/bin/dumpsys", "battery")
	if err != nil {
		return map[string]any{"error": "battery_query_failed", "message": "读取电量失败"}
	}
	level := -1
	status := -1
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "level:") {
			value := strings.TrimSpace(strings.TrimPrefix(line, "level:"))
			if parsed, parseErr := strconv.Atoi(value); parseErr == nil {
				level = parsed
			}
		}
		if strings.HasPrefix(line, "status:") {
			value := strings.TrimSpace(strings.TrimPrefix(line, "status:"))
			if parsed, parseErr := strconv.Atoi(value); parseErr == nil {
				status = parsed
			}
		}
	}
	charging := any(nil)
	switch status {
	case 2, 5:
		charging = true
	case 3, 4:
		charging = false
	}
	percent := any("unknown")
	if level >= 0 && level <= 100 {
		percent = level
	}
	return map[string]any{
		"battery_percent": percent,
		"charging":        charging,
		"collected_at":    time.Now().Unix(),
	}
}

func queryRootRecentSMS(ctx context.Context, config rootConfig) map[string]any {
	runner := newCommandSMSReader(config)
	ids, err := runner.ListIDs(ctx, 0, false)
	if err != nil {
		return map[string]any{"error": "sms_query_failed", "message": "读取短信列表失败"}
	}
	start := len(ids) - recentSMSLimit
	if start < 0 {
		start = 0
	}
	messages := make([]map[string]any, 0, len(ids)-start)
	for _, id := range ids[start:] {
		address, addressErr := runner.ReadField(ctx, id, "address")
		body, bodyErr := runner.ReadField(ctx, id, "body")
		date, dateErr := runner.ReadField(ctx, id, "date")
		if addressErr != nil || bodyErr != nil || dateErr != nil {
			continue
		}
		sender, senderErr := parseSingleField(address, "address")
		text, textErr := parseSingleField(body, "body")
		dateText, dateParseErr := parseSingleField(date, "date")
		if senderErr != nil || textErr != nil || dateParseErr != nil {
			continue
		}
		receivedAt, parseErr := strconv.ParseInt(strings.TrimSpace(dateText), 10, 64)
		if parseErr != nil {
			continue
		}
		messages = append(messages, map[string]any{
			"sender":      maskCommandSender(sender),
			"body":        truncateRunes(text, 200),
			"received_at": receivedAt / 1000,
		})
	}
	return map[string]any{"count": len(messages), "messages": messages}
}

func triggerRootUpload(ctx context.Context) map[string]any {
	if err := triggerCommandWorker(ctx); err != nil {
		return map[string]any{"error": "trigger_failed", "message": "触发上传失败"}
	}
	return map[string]any{"triggered_at": time.Now().Unix()}
}

func maskCommandSender(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return "未知发件人"
	}
	runes := []rune(value)
	if len(runes) <= 4 {
		return strings.Repeat("*", len(runes))
	}
	return strings.Repeat("*", len(runes)-4) + string(runes[len(runes)-4:])
}

func truncateRunes(value string, limit int) string {
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	return string(runes[:limit])
}
