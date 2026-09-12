//go:build sqlite_fallback

package main

import (
	"context"
	"database/sql"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	_ "modernc.org/sqlite"
)

func TestSQLiteFallbackPathAggregationDoesNotMaskPartialFailure(t *testing.T) {
	wantErr := errors.New("ce unavailable")
	err := scanSQLiteDatabasePaths([]string{"ce", "de"}, func(path string) error {
		if path == "ce" {
			return wantErr
		}
		return nil
	})
	if !errors.Is(err, wantErr) {
		t.Fatalf("error=%v want=%v", err, wantErr)
	}
}

func TestSQLiteFallbackErrorPreservesCompletedDatabaseState(t *testing.T) {
	wantErr := errors.New("de unavailable")
	err := runSQLiteFallbackErrorForTest([]string{"ce", "de"}, wantErr)
	var partial *sqliteFallbackError
	if !errors.As(err, &partial) || !partial.completed || !errors.Is(err, wantErr) {
		t.Fatalf("error=%v partial=%+v", err, partial)
	}
}

func runSQLiteFallbackErrorForTest(paths []string, failing error) error {
	completed := false
	err := scanSQLiteDatabasePaths(paths, func(path string) error {
		if path == "ce" {
			completed = true
			return nil
		}
		return failing
	})
	if err == nil {
		return nil
	}
	return &sqliteFallbackError{completed: completed, err: err}
}

func TestSQLiteDatabaseDirectoriesIncludeOnePlusDataPath(t *testing.T) {
	got := sqliteDatabaseDirectories(0)
	want := []string{
		"/data/user/0/com.android.providers.telephony/databases",
		"/data/data/com.android.providers.telephony/databases",
		"/data/user_de/0/com.android.providers.telephony/databases",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("directories=%v want=%v", got, want)
	}
}

func TestSQLiteFallbackReadsOnlyNewIncomingUndeletedRows(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "mmssms.db")
	database, err := sql.Open("sqlite", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	if _, err := database.Exec(`
		CREATE TABLE sms (
			_id INTEGER PRIMARY KEY,
			address TEXT,
			body TEXT,
			date INTEGER,
			type INTEGER,
			deleted INTEGER,
			sub_id INTEGER,
			sim_id INTEGER
		);
		INSERT INTO sms VALUES (11, 'sent', 'outgoing', 1786863717000, 2, 0, 5, 1);
		INSERT INTO sms VALUES (12, 'deleted', 'deleted', 1786863717100, 1, 1, 5, 1);
		INSERT INTO sms VALUES (13, '10690000', '验证码=482731, 第一行\n第二行', 1786863717139, 1, 0, 5, 1);
	`); err != nil {
		t.Fatal(err)
	}
	cursor := filepath.Join(t.TempDir(), "sqlite.cursor")
	if err := writeCursor(cursor, 10); err != nil {
		t.Fatal(err)
	}
	spool := t.TempDir()
	if err := scanSQLiteDatabase(context.Background(), databasePath, cursor, spool, "new_only", nil); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(spool)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("spool entries=%d", len(entries))
	}
	raw, err := os.ReadFile(filepath.Join(spool, spoolFileName("root:provider:13:1786863717139")))
	if err != nil {
		t.Fatal(err)
	}
	if !containsAll(raw, []byte("10690000"), []byte("验证码=482731"), []byte(`"subscription_id":5`)) {
		t.Fatalf("event=%s", raw)
	}
}

func TestSQLiteFallbackAcceptsNegativeSubscriptionSentinel(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "mmssms.db")
	database, err := sql.Open("sqlite", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.Exec(`
		CREATE TABLE sms (
			_id INTEGER PRIMARY KEY,
			address TEXT,
			body TEXT,
			date INTEGER,
			type INTEGER,
			deleted INTEGER,
			sub_id INTEGER,
			sim_id INTEGER
		);
		INSERT INTO sms VALUES (15639, '10690000', 'fixture', 1787220531701, 1, 0, -1, 5);
	`); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}

	cursor := filepath.Join(t.TempDir(), "sqlite.cursor")
	if err := writeCursor(cursor, 15638); err != nil {
		t.Fatal(err)
	}
	spool := t.TempDir()
	profiles := []simProfile{{SlotIndex: intPtr(0), SIMID: intPtr(5), CarrierName: "China Telecom"}}
	if err := scanSQLiteDatabase(context.Background(), databasePath, cursor, spool, "new_only", profiles); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(filepath.Join(spool, spoolFileName("root:provider:15639:1787220531701")))
	if err != nil {
		t.Fatal(err)
	}
	if !containsAll(raw, []byte(`"sim_slot":0`), []byte(`"sim_id":5`), []byte(`"carrier_name":"China Telecom"`)) {
		t.Fatalf("event=%s", raw)
	}
	got, err := readCursor(cursor)
	if err != nil {
		t.Fatal(err)
	}
	if got != 15639 {
		t.Fatalf("cursor=%d want=15639", got)
	}
}

func TestSQLiteEventUsesProviderIdentityAndResolvesOnePlusSlot(t *testing.T) {
	row := sqliteSMSRow{
		id:             3336,
		sender:         "10686571240040",
		body:           "fixture",
		dateMillis:     1787193013131,
		subscriptionID: intPtr(3),
	}
	event := sqliteRowEvent(row, []simProfile{
		{SlotIndex: intPtr(0), SubscriptionID: intPtr(3), CarrierName: "China Telecom"},
		{SlotIndex: intPtr(1), SubscriptionID: intPtr(2), CarrierName: "China Unicom"},
	})
	if event.ClientMessageID != "root:provider:3336:1787193013131" {
		t.Fatalf("client_message_id=%q", event.ClientMessageID)
	}
	if event.SIMSlot == nil || *event.SIMSlot != 0 || event.Metadata.CarrierName != "China Telecom" {
		t.Fatalf("event=%+v", event)
	}
}

func TestSQLiteFallbackRejectsMissingRequiredColumns(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "mmssms.db")
	database, err := sql.Open("sqlite", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.Exec(`CREATE TABLE sms (_id INTEGER, address TEXT, date INTEGER)`); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	if err := scanSQLiteDatabase(context.Background(), databasePath, filepath.Join(t.TempDir(), "cursor"), t.TempDir(), "new_only", nil); err == nil {
		t.Fatal("missing body column accepted")
	}
}

func TestSQLiteFallbackConnectionIsReadOnly(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "mmssms.db")
	database, err := sql.Open("sqlite", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.Exec(`CREATE TABLE sms (_id INTEGER PRIMARY KEY, address TEXT, body TEXT, date INTEGER)`); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}

	readOnly, err := openSQLiteReadOnly(databasePath)
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	if _, err := readOnly.Exec(`INSERT INTO sms VALUES (1, '10000', 'must-not-write', 1)`); err == nil {
		t.Fatal("read-only fallback accepted a database write")
	}
}

func TestSQLiteFallbackLoadsSIMProfilesOnlyWhenRowsExist(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "mmssms.db")
	database, err := sql.Open("sqlite", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.Exec(`
		CREATE TABLE sms (_id INTEGER PRIMARY KEY, address TEXT, body TEXT, date INTEGER, type INTEGER, sub_id INTEGER);
		INSERT INTO sms VALUES (1, '10000', 'old', 1000, 1, 3);
	`); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	cursorPath := filepath.Join(t.TempDir(), "cursor")
	if err := writeCursor(cursorPath, 1); err != nil {
		t.Fatal(err)
	}
	calls := 0
	loader := func(context.Context) []simProfile {
		calls++
		return []simProfile{{SlotIndex: intPtr(0), SubscriptionID: intPtr(3)}}
	}
	if err := scanSQLiteDatabaseWithProfileLoader(context.Background(), databasePath, cursorPath, t.TempDir(), "new_only", providerCursor{}, loader); err != nil {
		t.Fatal(err)
	}
	if calls != 0 {
		t.Fatalf("SIM profiles loaded without new rows: calls=%d", calls)
	}
}

func TestSQLiteFallbackRebaselinesAfterDatabaseIDResetWithoutSpoolingHistory(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "mmssms.db")
	database, err := sql.Open("sqlite", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.Exec(`
		CREATE TABLE sms (_id INTEGER PRIMARY KEY, address TEXT, body TEXT, date INTEGER, type INTEGER, deleted INTEGER);
		INSERT INTO sms VALUES (10, '10000', 'old-1', 1000, 1, 0);
		INSERT INTO sms VALUES (11, '10001', 'old-2', 2000, 1, 0);
	`); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}
	cursor := filepath.Join(t.TempDir(), "sqlite.cursor")
	if err := writeCursor(cursor, 3336); err != nil {
		t.Fatal(err)
	}
	spool := t.TempDir()
	if err := scanSQLiteDatabase(context.Background(), databasePath, cursor, spool, "new_only", nil); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(spool)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("historical rows were replayed: %d", len(entries))
	}
	got, err := readCursor(cursor)
	if err != nil {
		t.Fatal(err)
	}
	if got != 11 {
		t.Fatalf("cursor=%d want=11", got)
	}
}

func TestSQLiteFallbackUsesProviderBaselineToCaptureROMFilteredMessage(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "mmssms.db")
	database, err := sql.Open("sqlite", databasePath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := database.Exec(`
		CREATE TABLE sms (_id INTEGER PRIMARY KEY, address TEXT, body TEXT, date INTEGER, type INTEGER, deleted INTEGER, sub_id INTEGER);
		INSERT INTO sms VALUES (3335, '10000', 'baseline', 1000, 1, 0, 3);
		INSERT INTO sms VALUES (3336, '10001', 'filtered-by-rom', 2000, 1, 0, 3);
	`); err != nil {
		t.Fatal(err)
	}
	if err := database.Close(); err != nil {
		t.Fatal(err)
	}

	cursor := filepath.Join(t.TempDir(), "sqlite.cursor")
	spool := t.TempDir()
	baseline := providerCursor{ReceivedAtMillis: 1000, RowID: 3335}
	if err := scanSQLiteDatabaseWithProviderBaseline(context.Background(), databasePath, cursor, spool, "new_only", nil, baseline); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(spool)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != spoolFileName("root:provider:3336:2000") {
		t.Fatalf("spool entries=%v", entries)
	}
}

func TestSQLiteCursorPathSeparatesCEAndDEDatabases(t *testing.T) {
	stateDir := t.TempDir()
	ce := sqliteCursorPath(stateDir, "/data/user/0/com.android.providers.telephony/databases/mmssms.db")
	de := sqliteCursorPath(stateDir, "/data/user_de/0/com.android.providers.telephony/databases/mmssms.db")
	if ce == de {
		t.Fatalf("CE and DE cursor paths collide: %q", ce)
	}
	if filepath.Dir(ce) != stateDir || filepath.Dir(de) != stateDir {
		t.Fatalf("cursor escaped state directory: ce=%q de=%q", ce, de)
	}
}

func TestDiscoverSQLiteDatabasesDeduplicatesPathAliases(t *testing.T) {
	firstDir := t.TempDir()
	aliasDir := t.TempDir()
	firstPath := filepath.Join(firstDir, "mmssms.db")
	if err := os.WriteFile(firstPath, []byte("fixture"), 0o600); err != nil {
		t.Fatal(err)
	}
	aliasPath := filepath.Join(aliasDir, "mmssms.db")
	if err := os.Link(firstPath, aliasPath); err != nil {
		t.Fatal(err)
	}
	paths := discoverSQLiteDatabasesInDirectories([]string{firstDir, aliasDir})
	if !reflect.DeepEqual(paths, []string{firstPath}) {
		t.Fatalf("paths=%v", paths)
	}
}

func containsAll(value []byte, parts ...[]byte) bool {
	for _, part := range parts {
		if !bytesContains(value, part) {
			return false
		}
	}
	return true
}

func bytesContains(value []byte, part []byte) bool {
	for index := 0; index+len(part) <= len(value); index++ {
		matched := true
		for offset := range part {
			if value[index+offset] != part[offset] {
				matched = false
				break
			}
		}
		if matched {
			return true
		}
	}
	return len(part) == 0
}
