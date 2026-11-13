package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

type fakeRunner struct {
	listIDs []int64
}

func (runner *fakeRunner) ListIDs(context.Context, int64, bool) ([]int64, error) {
	return append([]int64(nil), runner.listIDs...), nil
}

func (runner *fakeRunner) ListIDsSince(context.Context, int64) ([]int64, error) {
	return append([]int64(nil), runner.listIDs...), nil
}

type cursorResetRunner struct {
	newIDs []int64
	allIDs []int64
}

func (runner *cursorResetRunner) ListIDs(_ context.Context, _ int64, newOnly bool) ([]int64, error) {
	if newOnly {
		return append([]int64(nil), runner.newIDs...), nil
	}
	return append([]int64(nil), runner.allIDs...), nil
}

func (runner *cursorResetRunner) ListIDsSince(context.Context, int64) ([]int64, error) {
	return append([]int64(nil), runner.newIDs...), nil
}

type fakeFieldRunner struct {
	listIDs []int64
	fields  map[int64]map[string][]byte
}

type fakeSIMProfileRunner struct {
	profiles []simProfile
	err      error
	calls    int
}

func (runner *fakeSIMProfileRunner) ListSIMProfiles(context.Context) ([]simProfile, error) {
	runner.calls++
	return append([]simProfile(nil), runner.profiles...), runner.err
}

type fakeCommandExecutor struct {
	responses [][]byte
	calls     [][]string
}

func (executor *fakeCommandExecutor) Run(_ context.Context, name string, args ...string) ([]byte, error) {
	call := append([]string{name}, args...)
	executor.calls = append(executor.calls, call)
	if len(executor.responses) == 0 {
		return nil, os.ErrNotExist
	}
	response := executor.responses[0]
	executor.responses = executor.responses[1:]
	return append([]byte(nil), response...), nil
}

func (runner *fakeFieldRunner) ListIDs(context.Context, int64, bool) ([]int64, error) {
	return append([]int64(nil), runner.listIDs...), nil
}

func (runner *fakeFieldRunner) ListIDsSince(context.Context, int64) ([]int64, error) {
	return append([]int64(nil), runner.listIDs...), nil
}

func (runner *fakeFieldRunner) ReadField(_ context.Context, rowID int64, field string) ([]byte, error) {
	row, ok := runner.fields[rowID]
	if !ok {
		return nil, os.ErrNotExist
	}
	raw, ok := row[field]
	if !ok {
		return nil, os.ErrNotExist
	}
	return append([]byte(nil), raw...), nil
}

func TestParseSingleFieldPreservesNewlinesDelimitersAndUnicode(t *testing.T) {
	raw := []byte("Row: 0 body=验证码=482731, 保留逗号\n第二行\n")

	got, err := parseSingleField(raw, "body")
	if err != nil {
		t.Fatal(err)
	}
	if got != "验证码=482731, 保留逗号\n第二行" {
		t.Fatalf("got %q", got)
	}
}

func TestProviderEventIDUsesSourceRowIdentity(t *testing.T) {
	first := eventID("provider", 15615, 1786863717139)
	second := eventID("provider", 15615, 1786863717139)

	if first != second {
		t.Fatalf("event id changed: %q != %q", first, second)
	}
	if first != "root:provider:15615:1786863717139" {
		t.Fatalf("event id=%q", first)
	}
}

func TestSigningMatchesBackendVector(t *testing.T) {
	body, err := os.ReadFile("../../tests/fixtures/message.json")
	if err != nil {
		t.Fatal(err)
	}
	secret, err := decodeSecret("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8")
	if err != nil {
		t.Fatal(err)
	}

	got := sign(secret, "1786883696", "root-module-vector-nonce-20260816", body)
	want := "wRuhc6pT3HQ9l_OjOdOqiqYRPREprqlFnPDx219l_H0"
	if got != want {
		t.Fatalf("signature=%q want=%q", got, want)
	}
}

func TestInitialSyncAdvancesCursorWithoutSpoolingHistoricalRows(t *testing.T) {
	spool := t.TempDir()
	cursor := filepath.Join(t.TempDir(), "provider.cursor")
	runner := &fakeRunner{listIDs: []int64{15613, 15614, 15615}}
	reader := providerReader{
		runner:     runner,
		fields:     &fakeFieldRunner{},
		cursorPath: cursor,
		now:        func() time.Time { return time.UnixMilli(1787039900000) },
	}

	if err := reader.scan(context.Background(), spool, "new_only"); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(spool)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("historical rows were spooled: %d", len(entries))
	}
	stored, err := readProviderCursor(cursor)
	if err != nil {
		t.Fatal(err)
	}
	if stored.ReceivedAtMillis != 1787039900000 || stored.RowID != 15615 {
		t.Fatalf("cursor=%+v", stored)
	}
}

func TestProviderScanMigratesLegacyCursorWithoutImportingRowsWhenProviderTailMovesBackwards(t *testing.T) {
	spool := t.TempDir()
	cursor := filepath.Join(t.TempDir(), "provider.cursor")
	if err := writeCursor(cursor, 3322); err != nil {
		t.Fatal(err)
	}
	runner := &cursorResetRunner{
		newIDs: nil,
		allIDs: []int64{130, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141},
	}
	reader := providerReader{
		runner:     runner,
		fields:     &fakeFieldRunner{},
		cursorPath: cursor,
		now:        func() time.Time { return time.UnixMilli(1787039900000) },
	}

	if err := reader.scan(context.Background(), spool, "new_only"); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(spool)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("historical rows were spooled after provider reset: %d", len(entries))
	}
	updated, err := readProviderCursor(cursor)
	if err != nil {
		t.Fatal(err)
	}
	if updated.ReceivedAtMillis != 1787039900000 || updated.RowID != 141 {
		t.Fatalf("cursor=%+v", updated)
	}
}

func TestProviderScanUsesReceivedTimeCursorAfterProviderTailReturns(t *testing.T) {
	spool := t.TempDir()
	cursor := filepath.Join(t.TempDir(), "provider.cursor")
	if err := writeProviderCursor(cursor, providerCursor{ReceivedAtMillis: 1787039900000, RowID: 141}); err != nil {
		t.Fatal(err)
	}
	runner := &fakeFieldRunner{
		listIDs: []int64{3323},
		fields: map[int64]map[string][]byte{
			3323: {
				"_id":     []byte("Row: 0 _id=3323\n"),
				"address": []byte("Row: 0 address=10690000\n"),
				"body":    []byte("Row: 0 body=验证码 482731\n"),
				"date":    []byte("Row: 0 date=1787039901000\n"),
			},
		},
	}
	reader := providerReader{runner: runner, fields: runner, cursorPath: cursor}

	if err := reader.scan(context.Background(), spool, "new_only"); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(spool)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("spool entries=%d", len(entries))
	}
	updated, err := readProviderCursor(cursor)
	if err != nil {
		t.Fatal(err)
	}
	if updated.ReceivedAtMillis != 1787039901000 || updated.RowID != 3323 {
		t.Fatalf("cursor=%+v", updated)
	}
}

func TestDecodeSecretRejectsNonProductionCredentials(t *testing.T) {
	cases := map[string]string{
		"padded":         "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
		"repeated bytes": base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{0x11}, 32)),
		"placeholder":    base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte("todo"), 8)),
	}
	for name, encoded := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := decodeSecret(encoded); err == nil {
				t.Fatal("credential was accepted")
			}
		})
	}
}

func TestValidateConfigAllowsIPHTTPSAndPrivateOptInCleartextHTTP(t *testing.T) {
	base := rootConfig{
		SchemaVersion:       2,
		Enabled:             true,
		DeviceID:            "00000000-0000-4000-8000-000000000001",
		HMACSecret:          "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
		Source:              "root_module",
		AndroidUserID:       0,
		InitialSyncMode:     "new_only",
		PollIntervalSeconds: 15,
		MaxBackoffSeconds:   300,
	}

	validHTTPSIP := base
	validHTTPSIP.BaseURL = "https://203.0.113.10:8443"
	if err := validateConfig(validHTTPSIP); err != nil {
		t.Fatalf("HTTPS IP should be accepted: %v", err)
	}

	for _, url := range []string{
		"http://127.0.0.1:35000",
		"http://192.168.2.109:8000",
		"http://10.0.0.1:8080",
		"http://[::1]:35000",
		"http://[fd00::1]:8080",
	} {
		optedIn := base
		optedIn.BaseURL = url
		optedIn.AllowInsecureLocalHTTP = true
		if err := validateConfig(optedIn); err != nil {
			t.Fatalf("opt-in cleartext HTTP %q should be accepted: %v", url, err)
		}
	}

	for _, url := range []string{
		"http://203.0.113.10:39300",
		"http://relay.example:39300",
		"http://127.0.0.1",
	} {
		optedIn := base
		optedIn.BaseURL = url
		optedIn.AllowInsecureLocalHTTP = true
		if err := validateConfig(optedIn); err == nil {
			t.Fatalf("unsafe cleartext HTTP %q was accepted", url)
		}
	}

	noOptIn := base
	noOptIn.BaseURL = "http://127.0.0.1:35000"
	if err := validateConfig(noOptIn); err == nil {
		t.Fatal("cleartext HTTP without opt-in was accepted")
	}
}

func TestValidateConfigRequiresHyphenatedUUIDDeviceID(t *testing.T) {
	base := rootConfig{
		SchemaVersion:       2,
		Enabled:             true,
		BaseURL:             "https://relay.example",
		DeviceID:            "00000000-0000-4000-8000-000000000001",
		HMACSecret:          "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
		Source:              "root_module",
		AndroidUserID:       0,
		InitialSyncMode:     "new_only",
		PollIntervalSeconds: 15,
		MaxBackoffSeconds:   300,
	}
	for _, deviceID := range []string{
		"device-1",
		"00000000000040008000000000000001",
		"{00000000-0000-4000-8000-000000000001}",
	} {
		config := base
		config.DeviceID = deviceID
		if err := validateConfig(config); err == nil {
			t.Fatalf("invalid device ID %q was accepted", deviceID)
		}
	}

	uppercase := base
	uppercase.DeviceID = "00000000-0000-4000-8000-00000000000A"
	if err := validateConfig(uppercase); err != nil {
		t.Fatalf("uppercase UUID should remain accepted: %v", err)
	}
}

func TestNewNonceUsesUnpaddedBase64URL(t *testing.T) {
	got, err := newNonce(bytes.NewReader(bytes.Repeat([]byte{0xa5}, 24)))
	if err != nil {
		t.Fatal(err)
	}
	if got != "paWlpaWlpaWlpaWlpaWlpaWlpaWlpaWl" {
		t.Fatalf("nonce=%q", got)
	}
	decoded, err := base64.RawURLEncoding.DecodeString(got)
	if err != nil {
		t.Fatal(err)
	}
	if len(decoded) != 24 {
		t.Fatalf("decoded nonce bytes=%d", len(decoded))
	}
}

func TestMarshalEventPreservesSMSAndContractFields(t *testing.T) {
	simSlot := 0
	subscriptionID := 5
	event := smsEvent{
		SchemaVersion:   1,
		ClientMessageID: "root:provider:15616:1786863717139",
		ReceivedAt:      "2026-08-18T00:00:00Z",
		Sender:          "10690000",
		Body:            "验证码=482731, 第一行\n第二行",
		Source:          "root_module",
		SIMSlot:         &simSlot,
		Metadata: eventMetadata{
			SourceMessageID: "15616",
			SubscriptionID:  &subscriptionID,
			CarrierName:     "fixture carrier",
		},
	}

	raw, err := marshalEvent(event)
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["client_message_id"] != event.ClientMessageID || decoded["body"] != event.Body {
		t.Fatalf("event changed during serialization: %s", raw)
	}
	metadata, ok := decoded["metadata"].(map[string]any)
	if !ok || metadata["source_message_id"] != "15616" || metadata["subscription_id"] != float64(5) {
		t.Fatalf("metadata=%v", decoded["metadata"])
	}
}

func TestProviderScanSpoolsNewSMSAtomically(t *testing.T) {
	spool := t.TempDir()
	cursor := filepath.Join(t.TempDir(), "provider.cursor")
	if err := writeProviderCursor(cursor, providerCursor{ReceivedAtMillis: 1786863717000, RowID: 15615}); err != nil {
		t.Fatal(err)
	}
	runner := &fakeFieldRunner{
		listIDs: []int64{15617, 15616},
		fields: map[int64]map[string][]byte{
			15616: {
				"_id":          []byte("Row: 0 _id=15616\n"),
				"address":      []byte("Row: 0 address=10690000\n"),
				"body":         []byte("Row: 0 body=验证码=482731, 第一行\r\n第二行\n"),
				"date":         []byte("Row: 0 date=1786863717139\n"),
				"sub_id":       []byte("Row: 0 sub_id=5\n"),
				"carrier_name": []byte("Row: 0 carrier_name=fixture carrier\n"),
				"slot_index":   []byte("Row: 0 slot_index=0\n"),
			},
			15617: {
				"_id":     []byte("Row: 0 _id=15617\n"),
				"address": []byte("Row: 0 address=10086\n"),
				"body":    []byte("Row: 0 body=second\n"),
				"date":    []byte("Row: 0 date=1786863718140\n"),
			},
		},
	}
	reader := providerReader{runner: runner, fields: runner, cursorPath: cursor}
	if err := reader.scan(context.Background(), spool, "new_only"); err != nil {
		t.Fatal(err)
	}

	firstEventID := "root:provider:15616:1786863717139"
	first, err := os.ReadFile(filepath.Join(spool, spoolFileName(firstEventID)))
	if err != nil {
		t.Fatal(err)
	}
	var event map[string]any
	if err := json.Unmarshal(first, &event); err != nil {
		t.Fatal(err)
	}
	if event["body"] != "验证码=482731, 第一行\r\n第二行" || event["sender"] != "10690000" {
		t.Fatalf("event=%s", first)
	}
	if event["sim_slot"] != float64(0) {
		t.Fatalf("sim_slot=%v", event["sim_slot"])
	}
	entries, err := os.ReadDir(spool)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 {
		t.Fatalf("spool entries=%d", len(entries))
	}
	updatedCursor, err := readProviderCursor(cursor)
	if err != nil {
		t.Fatal(err)
	}
	if updatedCursor.ReceivedAtMillis != 1786863718140 || updatedCursor.RowID != 15617 {
		t.Fatalf("cursor=%+v", updatedCursor)
	}
}

func TestProviderScanLoadsSIMProfilesOnlyOncePerBatch(t *testing.T) {
	spool := t.TempDir()
	cursor := filepath.Join(t.TempDir(), "provider.cursor")
	if err := writeProviderCursor(cursor, providerCursor{ReceivedAtMillis: 1786863717000, RowID: 15615}); err != nil {
		t.Fatal(err)
	}
	fields := &fakeFieldRunner{
		listIDs: []int64{15616, 15617},
		fields: map[int64]map[string][]byte{
			15616: {
				"_id":     []byte("Row: 0 _id=15616\n"),
				"address": []byte("Row: 0 address=10690000\n"),
				"body":    []byte("Row: 0 body=first\n"),
				"date":    []byte("Row: 0 date=1786863717139\n"),
				"sub_id":  []byte("Row: 0 sub_id=3\n"),
			},
			15617: {
				"_id":     []byte("Row: 0 _id=15617\n"),
				"address": []byte("Row: 0 address=10086\n"),
				"body":    []byte("Row: 0 body=second\n"),
				"date":    []byte("Row: 0 date=1786863718140\n"),
				"sub_id":  []byte("Row: 0 sub_id=3\n"),
			},
		},
	}
	slot := 0
	subscriptionID := 3
	profiles := &fakeSIMProfileRunner{profiles: []simProfile{{
		SlotIndex:      &slot,
		SubscriptionID: &subscriptionID,
		PhoneNumber:    "+8618130000000",
	}}}
	reader := providerReader{runner: fields, fields: fields, simProfiles: profiles, cursorPath: cursor}
	if err := reader.scan(context.Background(), spool, "new_only"); err != nil {
		t.Fatal(err)
	}
	if profiles.calls != 1 {
		t.Fatalf("SIM profile queries=%d want=1", profiles.calls)
	}
}

func TestWriteSpoolEventDoesNotOverwriteOrLeaveTemporaryFiles(t *testing.T) {
	spool := t.TempDir()
	event := smsEvent{
		SchemaVersion:   1,
		ClientMessageID: "root:provider:200:1786863719000",
		ReceivedAt:      "2026-08-18T00:00:00Z",
		Sender:          "10690000",
		Body:            "original",
		Source:          "root_module",
		Metadata:        eventMetadata{SourceMessageID: "200"},
	}
	if err := writeSpoolEvent(spool, event); err != nil {
		t.Fatal(err)
	}
	event.Body = "replacement"
	if err := writeSpoolEvent(spool, event); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(spool)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("spool entries=%d", len(entries))
	}
	raw, err := os.ReadFile(filepath.Join(spool, spoolFileName(event.ClientMessageID)))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("replacement")) || !bytes.Contains(raw, []byte("original")) {
		t.Fatalf("existing event changed: %s", raw)
	}
}

func TestContentProviderListIDsUsesArgumentArrayAndFiltersCursor(t *testing.T) {
	executor := &fakeCommandExecutor{responses: [][]byte{
		[]byte("Row: 0 _id=15617\nRow: 1 _id=15615\nRow: 2 _id=15616\n"),
	}}
	runner := contentProviderRunner{executor: executor, binary: "/system/bin/content", userID: 0}

	got, err := runner.ListIDs(context.Background(), 15615, true)
	if err != nil {
		t.Fatal(err)
	}
	if want := []int64{15616, 15617}; !reflect.DeepEqual(got, want) {
		t.Fatalf("ids=%v want=%v", got, want)
	}
	wantCall := []string{
		"/system/bin/content", "query", "--uri", "content://sms/inbox", "--user", "0",
		"--projection", "_id", "--where", "_id>15615", "--sort", "date ASC,_id ASC",
	}
	if !reflect.DeepEqual(executor.calls, [][]string{wantCall}) {
		t.Fatalf("calls=%v", executor.calls)
	}
}

func TestContentProviderListsIDsByReceivedTime(t *testing.T) {
	executor := &fakeCommandExecutor{responses: [][]byte{
		[]byte("Row: 0 _id=15617\nRow: 1 _id=15616\n"),
	}}
	runner := contentProviderRunner{executor: executor, binary: "/system/bin/content", userID: 0}

	got, err := runner.ListIDsSince(context.Background(), 1786863717000)
	if err != nil {
		t.Fatal(err)
	}
	if want := []int64{15616, 15617}; !reflect.DeepEqual(got, want) {
		t.Fatalf("ids=%v want=%v", got, want)
	}
	wantCall := []string{
		"/system/bin/content", "query", "--uri", "content://sms/inbox", "--user", "0",
		"--projection", "_id", "--where", "date>=1786863717000", "--sort", "date ASC,_id ASC",
	}
	if !reflect.DeepEqual(executor.calls, [][]string{wantCall}) {
		t.Fatalf("calls=%v", executor.calls)
	}
}

func TestContentProviderListsIDsStrictlyAfterWatermark(t *testing.T) {
	executor := &fakeCommandExecutor{responses: [][]byte{
		[]byte("Row: 0 _id=15617\n"),
	}}
	runner := contentProviderRunner{executor: executor, binary: "/system/bin/content", userID: 0}

	got, err := runner.ListIDsAfter(context.Background(), providerCursor{
		ReceivedAtMillis: 1786863717000,
		RowID:            15616,
	})
	if err != nil {
		t.Fatal(err)
	}
	if want := []int64{15617}; !reflect.DeepEqual(got, want) {
		t.Fatalf("ids=%v want=%v", got, want)
	}
	wantCall := []string{
		"/system/bin/content", "query", "--uri", "content://sms/inbox", "--user", "0",
		"--projection", "_id", "--where", "(date>1786863717000 OR (date=1786863717000 AND _id>15616))", "--sort", "date ASC,_id ASC",
	}
	if !reflect.DeepEqual(executor.calls, [][]string{wantCall}) {
		t.Fatalf("calls=%v", executor.calls)
	}
}

func TestProviderEmptyResultFallsBackToDatabase(t *testing.T) {
	providerCalled := false
	fallbackCalled := false
	err := runProviderThenFallback(
		context.Background(),
		func(context.Context) (bool, error) {
			providerCalled = true
			return false, nil
		},
		func(context.Context) error {
			fallbackCalled = true
			return nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if !providerCalled || !fallbackCalled {
		t.Fatalf("provider_called=%v fallback_called=%v", providerCalled, fallbackCalled)
	}
}

func TestProviderRowsStillRunFallback(t *testing.T) {
	fallbackCalled := false
	err := runProviderThenFallback(
		context.Background(),
		func(context.Context) (bool, error) { return true, nil },
		func(context.Context) error {
			fallbackCalled = true
			return nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if !fallbackCalled {
		t.Fatal("database fallback must supplement provider rows that the ROM may filter")
	}
}

func TestContentProviderReadsOneFieldWithoutParsingItsValue(t *testing.T) {
	raw := []byte("Row: 0 body=验证码=482731, 第一行\nRow: 1 is literal SMS text\n")
	executor := &fakeCommandExecutor{responses: [][]byte{raw}}
	runner := contentProviderRunner{executor: executor, binary: "/system/bin/content", userID: 10}

	got, err := runner.ReadField(context.Background(), 15616, "body")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, raw) {
		t.Fatalf("raw field changed: %q", got)
	}
	wantCall := []string{
		"/system/bin/content", "query", "--uri", "content://sms/inbox", "--user", "10",
		"--projection", "body", "--where", "_id=15616",
	}
	if !reflect.DeepEqual(executor.calls, [][]string{wantCall}) {
		t.Fatalf("calls=%v", executor.calls)
	}
}

func TestContentProviderFallsBackToDumpsysForSubscriptionSlotMapping(t *testing.T) {
	executor := &fakeCommandExecutor{responses: [][]byte{
		[]byte("No result found.\n"),
		[]byte("Active subscription info list:\n{id=5 iccId=8986 simSlotIndex=0 carrierId=2237 carrierName=China mNumber=[****]}\n{id=6 iccId=8986 simSlotIndex=1 carrierId=2237 carrierName=China mNumber=[****]}\n"),
	}}
	runner := contentProviderRunner{
		executor:      executor,
		binary:        "/system/bin/content",
		dumpsysBinary: "/system/bin/dumpsys",
		userID:        0,
	}

	profiles, err := runner.ListSIMProfiles(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(profiles) != 2 || profiles[0].SubscriptionID == nil || *profiles[0].SubscriptionID != 5 || profiles[0].SlotIndex == nil || *profiles[0].SlotIndex != 0 || profiles[1].SubscriptionID == nil || *profiles[1].SubscriptionID != 6 || profiles[1].SlotIndex == nil || *profiles[1].SlotIndex != 1 {
		t.Fatalf("profiles=%+v", profiles)
	}
	wantCalls := [][]string{
		{"/system/bin/content", "query", "--uri", "content://telephony/siminfo", "--user", "0", "--projection", "_id", "--sort", "_id ASC"},
		{"/system/bin/dumpsys", "isub"},
	}
	if !reflect.DeepEqual(executor.calls, wantCalls) {
		t.Fatalf("calls=%v", executor.calls)
	}
}

func TestContentProviderMapsOnePlusSIMIDToSlotWithoutSlotIndexColumn(t *testing.T) {
	executor := &fakeCommandExecutor{responses: [][]byte{
		[]byte("Row: 0 _id=2\nRow: 1 _id=3\n"),
		[]byte("No result found.\n"),
		[]byte("Row: 0 sim_id=1\n"),
		[]byte("No result found.\n"),
		[]byte("Row: 0 _id=2\n"),
		[]byte("Row: 0 number=+861855535671\n"),
		[]byte("Row: 0 carrier_name=China Unicom\n"),
		[]byte("No result found.\n"),
		[]byte("Row: 0 sim_id=0\n"),
		[]byte("No result found.\n"),
		[]byte("Row: 0 _id=3\n"),
		[]byte("Row: 0 number=+8613800138002\n"),
		[]byte("Row: 0 carrier_name=China Telecom\n"),
	}}
	runner := contentProviderRunner{executor: executor, binary: "/system/bin/content", userID: 0}

	profiles, err := runner.listSIMProfilesFromProvider(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(profiles) != 2 {
		t.Fatalf("profiles=%+v", profiles)
	}
	if profiles[0].SubscriptionID == nil || *profiles[0].SubscriptionID != 2 ||
		profiles[0].SlotIndex == nil || *profiles[0].SlotIndex != 1 ||
		profiles[0].PhoneNumber != "+861855535671" {
		t.Fatalf("SIM 2 profile=%+v", profiles[0])
	}
	if profiles[1].SubscriptionID == nil || *profiles[1].SubscriptionID != 3 ||
		profiles[1].SlotIndex == nil || *profiles[1].SlotIndex != 0 ||
		profiles[1].PhoneNumber != "+8613800138002" {
		t.Fatalf("SIM 1 profile=%+v", profiles[1])
	}
}

func TestContentProviderEnrichesVendorSubscriptionWithDumpsysSlot(t *testing.T) {
	executor := &fakeCommandExecutor{responses: [][]byte{
		[]byte("Row: 0 _id=6\n"),
		[]byte("No result found.\n"),
		[]byte("Row: 0 sim_id=6\n"),
		[]byte("No result found.\n"),
		[]byte("Row: 0 _id=6\n"),
		[]byte("Row: 0 number=+8618555000006\n"),
		[]byte("Row: 0 carrier_name=fixture carrier\n"),
		[]byte("Active subscription info list:\n{id=6 simSlotIndex=1 carrierName=fixture number=[****]}\n"),
	}}
	runner := contentProviderRunner{
		executor:      executor,
		binary:        "/system/bin/content",
		dumpsysBinary: "/system/bin/dumpsys",
		userID:        0,
	}

	profiles, err := runner.ListSIMProfiles(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(profiles) != 1 || profiles[0].SlotIndex == nil || *profiles[0].SlotIndex != 1 ||
		profiles[0].SubscriptionID == nil || *profiles[0].SubscriptionID != 6 ||
		profiles[0].PhoneNumber != "+8618555000006" {
		t.Fatalf("profiles=%+v", profiles)
	}
}

func TestProviderSkipsMissingOptionalFieldsButRejectsMalformedRequiredDate(t *testing.T) {
	tests := []struct {
		name       string
		date       string
		wantEvents int
		wantError  bool
	}{
		{name: "optional fields missing", date: "1786863717139", wantEvents: 1},
		{name: "malformed date", date: "not-a-date", wantEvents: 0, wantError: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			spool := t.TempDir()
			cursor := filepath.Join(t.TempDir(), "provider.cursor")
			if err := writeProviderCursor(cursor, providerCursor{ReceivedAtMillis: 1786863717000, RowID: 15615}); err != nil {
				t.Fatal(err)
			}
			runner := &fakeFieldRunner{
				listIDs: []int64{15616},
				fields: map[int64]map[string][]byte{
					15616: {
						"_id":     []byte("Row: 0 _id=15616\n"),
						"address": []byte("Row: 0 address=10690000\n"),
						"body":    []byte("Row: 0 body=fixture\n"),
						"date":    []byte("Row: 0 date=" + tc.date + "\n"),
					},
				},
			}
			reader := providerReader{runner: runner, fields: runner, cursorPath: cursor}
			err := reader.scan(context.Background(), spool, "new_only")
			if (err != nil) != tc.wantError {
				t.Fatalf("err=%v wantError=%v", err, tc.wantError)
			}
			entries, readErr := os.ReadDir(spool)
			if readErr != nil {
				t.Fatal(readErr)
			}
			if len(entries) != tc.wantEvents {
				t.Fatalf("spool entries=%d want=%d", len(entries), tc.wantEvents)
			}
			if !tc.wantError {
				raw, err := os.ReadFile(filepath.Join(spool, spoolFileName("root:provider:15616:1786863717139")))
				if err != nil {
					t.Fatal(err)
				}
				if bytes.Contains(raw, []byte("subscription_id")) || bytes.Contains(raw, []byte("carrier_name")) {
					t.Fatalf("optional fields unexpectedly present: %s", raw)
				}
			}
		})
	}
}

func TestProviderAddsResolvedSIMSnapshotToEventMetadata(t *testing.T) {
	spool := t.TempDir()
	cursor := filepath.Join(t.TempDir(), "provider.cursor")
	if err := writeProviderCursor(cursor, providerCursor{ReceivedAtMillis: 1786863717000, RowID: 15615}); err != nil {
		t.Fatal(err)
	}
	runner := &fakeFieldRunner{
		listIDs: []int64{15616},
		fields: map[int64]map[string][]byte{
			15616: {
				"_id":     []byte("Row: 0 _id=15616\n"),
				"address": []byte("Row: 0 address=10690000\n"),
				"body":    []byte("Row: 0 body=fixture\n"),
				"date":    []byte("Row: 0 date=1786863717139\n"),
				"sub_id":  []byte("Row: 0 sub_id=5\n"),
			},
		},
	}
	simRunner := &fakeSIMProfileRunner{profiles: []simProfile{
		{SlotIndex: intPtr(0), SubscriptionID: intPtr(5), PhoneNumber: "+15550100005", CarrierName: "fixture carrier"},
	}}
	reader := providerReader{runner: runner, fields: runner, simProfiles: simRunner, cursorPath: cursor}
	if err := reader.scan(context.Background(), spool, "new_only"); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(spool, spoolFileName("root:provider:15616:1786863717139")))
	if err != nil {
		t.Fatal(err)
	}
	var event map[string]any
	if err := json.Unmarshal(raw, &event); err != nil {
		t.Fatal(err)
	}
	metadata := event["metadata"].(map[string]any)
	if metadata["phone_number"] != "+15550100005" || metadata["carrier_name"] != "fixture carrier" {
		t.Fatalf("metadata=%v", metadata)
	}
}

func TestParseReadOptionsRequiresPrivateDirectoriesAndNonNegativeUser(t *testing.T) {
	got, err := parseReadOptions([]string{
		"--config", "/data/adb/sms-relay/config.json",
		"--state-dir", "/data/adb/sms-relay/reader",
		"--spool-dir", "/data/adb/sms-relay/spool",
		"--user-id", "0",
	})
	if err != nil {
		t.Fatal(err)
	}
	if got.configPath != "/data/adb/sms-relay/config.json" || got.stateDir != "/data/adb/sms-relay/reader" || got.spoolDir != "/data/adb/sms-relay/spool" || got.userID != 0 {
		t.Fatalf("options=%+v", got)
	}
	for _, args := range [][]string{
		{"--state-dir", "/state", "--spool-dir", "/spool"},
		{"--config", "/config", "--state-dir", "/state", "--spool-dir", "/spool", "--user-id", "-1"},
	} {
		if _, err := parseReadOptions(args); err == nil {
			t.Fatalf("accepted invalid args=%v", args)
		}
	}
}

func TestValidateManualSQLitePathRestrictsTelephonyProviderDatabase(t *testing.T) {
	for _, value := range []string{
		"",
		"/data/user/0/com.android.providers.telephony/databases/mmssms.db",
		"/data/user_de/10/com.android.providers.telephony/databases/mmssms.db",
		"/data/data/com.android.providers.telephony/databases/mmssms.db",
	} {
		if err := validateManualSQLitePath(value); err != nil {
			t.Fatalf("path %q rejected: %v", value, err)
		}
	}
	for _, value := range []string{
		"data/user/0/com.android.providers.telephony/databases/mmssms.db",
		"/data/user/0/com.android.providers.telephony/databases/sms.db",
		"/data/user/0/com.android.providers.telephony/databases/../mmssms.db",
		"/sdcard/mmssms.db",
	} {
		if err := validateManualSQLitePath(value); err == nil {
			t.Fatalf("path %q accepted", value)
		}
	}
}

func TestHashAndHMACFileMatchBackendVector(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "body.json")
	body, err := os.ReadFile("../../tests/fixtures/message.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(bodyPath, body, 0o600); err != nil {
		t.Fatal(err)
	}
	secret, err := decodeSecret("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8")
	if err != nil {
		t.Fatal(err)
	}
	hash, err := hashFile(bodyPath)
	if err != nil {
		t.Fatal(err)
	}
	if hash != "5401bf98eb658b87273e66d2cfcc0613704301ba56ac8278b04b8ab2331c4f57" {
		t.Fatalf("hash=%q", hash)
	}
	got, err := hmacFile(secret, "1786883696", "root-module-vector-nonce-20260816", bodyPath)
	if err != nil {
		t.Fatal(err)
	}
	if got != "wRuhc6pT3HQ9l_OjOdOqiqYRPREprqlFnPDx219l_H0" {
		t.Fatalf("signature=%q", got)
	}
}
