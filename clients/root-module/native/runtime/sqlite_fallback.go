//go:build sqlite_fallback

package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

type sqliteSchema struct {
	columns map[string]bool
}

type sqliteDatabaseInfo struct {
	Path    string
	MaxID   int64
	Columns []string
	Rows    int64
}

type sqliteSMSRow struct {
	id             int64
	sender         string
	body           string
	dateMillis     int64
	subscriptionID *int
	simID          *int
}

func runSQLiteFallback(ctx context.Context, options readOptions, baseline providerCursor) error {
	paths := discoverSQLiteDatabases(options.userID)
	if options.databasePath != "" {
		paths = []string{options.databasePath}
	}
	if len(paths) == 0 {
		return fmt.Errorf("database_fallback_unavailable")
	}
	profilesLoaded := false
	profiles := []simProfile(nil)
	loadProfiles := func(loadCtx context.Context) []simProfile {
		if profilesLoaded {
			return profiles
		}
		profilesLoaded = true
		if raw, err := (execCommandExecutor{}).Run(loadCtx, "/system/bin/dumpsys", "isub"); err == nil {
			profiles = parseDumpsysSubscriptionProfiles(raw)
		}
		return profiles
	}
	completed := false
	diagnosticWritten := false
	scanErr := scanSQLiteDatabasePaths(paths, func(path string) error {
		info, validationErr := validateSQLiteDatabase(ctx, path)
		if validationErr != nil {
			return validationErr
		}
		cursorPath := sqliteCursorPath(options.stateDir, path)
		err := scanSQLiteDatabaseWithProfileLoader(ctx, path, cursorPath, options.spoolDir, "new_only", baseline, loadProfiles)
		if err == nil {
			completed = true
			cursor, _ := readCursor(cursorPath)
			if !diagnosticWritten {
				writeReaderDiagnostic(options.stateDir, readerDiagnostic{
					Mode: "SQLite", Path: path, Source: databaseSource(options.databasePath),
					MaxID: info.MaxID, Cursor: cursor, Columns: info.Columns,
				})
				diagnosticWritten = true
			}
		}
		return err
	})
	if scanErr == nil {
		return nil
	}
	return &sqliteFallbackError{completed: completed, err: scanErr}
}

func validateSQLiteDatabase(ctx context.Context, databasePath string) (sqliteDatabaseInfo, error) {
	if err := validateManualSQLitePath(databasePath); err != nil {
		return sqliteDatabaseInfo{}, err
	}
	resolved, err := filepath.EvalSymlinks(databasePath)
	if err != nil {
		return sqliteDatabaseInfo{}, err
	}
	if err := validateManualSQLitePath(filepath.ToSlash(resolved)); err != nil {
		return sqliteDatabaseInfo{}, err
	}
	info, err := os.Stat(databasePath)
	if err != nil {
		return sqliteDatabaseInfo{}, err
	}
	if !info.Mode().IsRegular() {
		return sqliteDatabaseInfo{}, fmt.Errorf("sqlite database is not a regular file")
	}
	database, err := openSQLiteReadOnly(databasePath)
	if err != nil {
		return sqliteDatabaseInfo{}, err
	}
	defer database.Close()
	schema, err := discoverSQLiteSchema(ctx, database)
	if err != nil {
		return sqliteDatabaseInfo{}, err
	}
	maxID, err := querySQLiteMaxID(ctx, database, schema)
	if err != nil {
		return sqliteDatabaseInfo{}, err
	}
	var rows int64
	if err := database.QueryRowContext(ctx, `SELECT COUNT(*) FROM "sms"`).Scan(&rows); err != nil {
		return sqliteDatabaseInfo{}, err
	}
	columns := make([]string, 0, len(schema.columns))
	for column := range schema.columns {
		columns = append(columns, column)
	}
	sort.Strings(columns)
	return sqliteDatabaseInfo{Path: databasePath, MaxID: maxID, Columns: columns, Rows: rows}, nil
}

func scanSQLiteDatabasePaths(paths []string, scan func(string) error) error {
	if len(paths) == 0 {
		return fmt.Errorf("database_fallback_unavailable")
	}
	var firstErr error
	for _, path := range paths {
		if err := scan(path); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

func sqliteDatabaseDirectories(userID int) []string {
	directories := []string{
		fmt.Sprintf("/data/user/%d/com.android.providers.telephony/databases", userID),
	}
	if userID == 0 {
		directories = append(directories, "/data/data/com.android.providers.telephony/databases")
	}
	return append(directories, fmt.Sprintf("/data/user_de/%d/com.android.providers.telephony/databases", userID))
}

func discoverSQLiteDatabases(userID int) []string {
	paths := discoverSQLiteDatabasesInDirectories(sqliteDatabaseDirectories(userID))
	type scoredPath struct {
		path  string
		maxID int64
		index int
	}
	scored := make([]scoredPath, 0, len(paths))
	for index, path := range paths {
		info, err := validateSQLiteDatabase(context.Background(), path)
		if err != nil {
			continue
		}
		scored = append(scored, scoredPath{path: path, maxID: info.MaxID, index: index})
	}
	sort.SliceStable(scored, func(left, right int) bool {
		leftPopulated := scored[left].maxID > 0
		rightPopulated := scored[right].maxID > 0
		if leftPopulated != rightPopulated {
			return leftPopulated
		}
		return scored[left].index < scored[right].index
	})
	result := make([]string, 0, len(scored))
	for _, item := range scored {
		result = append(result, item.path)
	}
	return result
}

func discoverSQLiteDatabasesInDirectories(directories []string) []string {
	seen := make(map[string]bool)
	seenFiles := make([]os.FileInfo, 0)
	paths := make([]string, 0)
	for _, directory := range directories {
		entries, err := os.ReadDir(directory)
		if err != nil {
			continue
		}
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".db") {
				continue
			}
			path := filepath.Join(directory, entry.Name())
			if entry.Name() != "mmssms.db" && entry.Name() != "sms.db" {
				continue
			}
			canonical := path
			if resolved, resolveErr := filepath.EvalSymlinks(path); resolveErr == nil {
				canonical = resolved
			}
			if seen[canonical] {
				continue
			}
			info, statErr := os.Stat(path)
			if statErr != nil {
				continue
			}
			duplicateFile := false
			for _, seenInfo := range seenFiles {
				if os.SameFile(info, seenInfo) {
					duplicateFile = true
					break
				}
			}
			if duplicateFile {
				continue
			}
			seen[canonical] = true
			seenFiles = append(seenFiles, info)
			paths = append(paths, path)
		}
	}
	return paths
}

func scanSQLiteDatabase(ctx context.Context, databasePath, cursorPath, spoolDir, initialSyncMode string, simProfiles ...[]simProfile) error {
	var profiles []simProfile
	if len(simProfiles) > 0 {
		profiles = simProfiles[0]
	}
	return scanSQLiteDatabaseWithProfileLoader(ctx, databasePath, cursorPath, spoolDir, initialSyncMode, providerCursor{}, func(context.Context) []simProfile { return profiles })
}

func scanSQLiteDatabaseWithProviderBaseline(ctx context.Context, databasePath, cursorPath, spoolDir, initialSyncMode string, simProfiles []simProfile, baseline providerCursor) error {
	return scanSQLiteDatabaseWithProfileLoader(ctx, databasePath, cursorPath, spoolDir, initialSyncMode, baseline, func(context.Context) []simProfile { return simProfiles })
}

func scanSQLiteDatabaseWithProfileLoader(ctx context.Context, databasePath, cursorPath, spoolDir, initialSyncMode string, baseline providerCursor, loadProfiles func(context.Context) []simProfile) error {
	if initialSyncMode != "new_only" {
		return fmt.Errorf("unsupported initial sync mode")
	}
	database, err := openSQLiteReadOnly(databasePath)
	if err != nil {
		return err
	}
	defer database.Close()
	schema, err := discoverSQLiteSchema(ctx, database)
	if err != nil {
		return err
	}
	cursor, err := readCursor(cursorPath)
	if err != nil {
		if !isMissingFile(err) {
			return err
		}
		if baseline.ReceivedAtMillis > 0 {
			rows, queryErr := querySQLiteRowsAfterWatermark(ctx, database, schema, baseline)
			if queryErr != nil {
				return queryErr
			}
			if len(rows) == 0 {
				maxID, maxErr := querySQLiteMaxID(ctx, database, schema)
				if maxErr != nil {
					return maxErr
				}
				return writeCursor(cursorPath, maxID)
			}
			profiles := loadProfiles(ctx)
			for _, row := range rows {
				if err := writeSpoolEvent(spoolDir, sqliteRowEvent(row, profiles)); err != nil {
					return err
				}
				if err := writeCursor(cursorPath, row.id); err != nil {
					return err
				}
			}
			return nil
		}
		rows, err := querySQLiteRows(ctx, database, schema, 0, false)
		if err != nil {
			return err
		}
		if len(rows) == 0 {
			return writeCursor(cursorPath, 0)
		}
		return writeCursor(cursorPath, rows[len(rows)-1].id)
	}
	maxID, err := querySQLiteMaxID(ctx, database, schema)
	if err != nil {
		return err
	}
	if maxID < cursor {
		return writeCursor(cursorPath, maxID)
	}
	rows, err := querySQLiteRows(ctx, database, schema, cursor, true)
	if err != nil {
		return err
	}
	if len(rows) == 0 {
		return nil
	}
	profiles := loadProfiles(ctx)
	for _, row := range rows {
		event := sqliteRowEvent(row, profiles)
		if err := writeSpoolEvent(spoolDir, event); err != nil {
			return err
		}
		if err := writeCursor(cursorPath, row.id); err != nil {
			return err
		}
	}
	return nil
}

func openSQLiteReadOnly(databasePath string) (*sql.DB, error) {
	databaseURLPath := filepath.ToSlash(databasePath)
	if filepath.VolumeName(databasePath) != "" && !strings.HasPrefix(databaseURLPath, "/") {
		databaseURLPath = "/" + databaseURLPath
	}
	databaseURL := &url.URL{Scheme: "file", Path: databaseURLPath}
	query := databaseURL.Query()
	query.Set("mode", "ro")
	query.Add("_pragma", "query_only(1)")
	query.Add("_pragma", "busy_timeout(5000)")
	databaseURL.RawQuery = query.Encode()
	return sql.Open("sqlite", databaseURL.String())
}

func sqliteCursorPath(stateDir, databasePath string) string {
	digest := sha256.Sum256([]byte(databasePath))
	return filepath.Join(stateDir, fmt.Sprintf("sqlite-%x.cursor", digest[:8]))
}

func querySQLiteMaxID(ctx context.Context, database *sql.DB, schema sqliteSchema) (int64, error) {
	var value any
	if err := database.QueryRowContext(ctx, `SELECT COALESCE(MAX("_id"), 0) FROM "sms"`).Scan(&value); err != nil {
		return 0, err
	}
	return sqliteInt64(value)
}

func isMissingFile(err error) bool {
	return errors.Is(err, os.ErrNotExist)
}

func discoverSQLiteSchema(ctx context.Context, database *sql.DB) (sqliteSchema, error) {
	rows, err := database.QueryContext(ctx, `PRAGMA table_info("sms")`)
	if err != nil {
		return sqliteSchema{}, err
	}
	defer rows.Close()
	columns := make(map[string]bool)
	for rows.Next() {
		var cid int
		var name, columnType string
		var notNull, primaryKey int
		var defaultValue any
		if err := rows.Scan(&cid, &name, &columnType, &notNull, &defaultValue, &primaryKey); err != nil {
			return sqliteSchema{}, err
		}
		columns[name] = true
	}
	if err := rows.Err(); err != nil {
		return sqliteSchema{}, err
	}
	for _, required := range []string{"_id", "address", "body", "date"} {
		if !columns[required] {
			return sqliteSchema{}, fmt.Errorf("sqlite sms table missing required column")
		}
	}
	return sqliteSchema{columns: columns}, nil
}

func querySQLiteRows(ctx context.Context, database *sql.DB, schema sqliteSchema, cursor int64, newOnly bool) ([]sqliteSMSRow, error) {
	if newOnly {
		return querySQLiteRowsWithCondition(ctx, database, schema, `"_id" > ?`, []any{cursor})
	}
	return querySQLiteRowsWithCondition(ctx, database, schema, "", nil)
}

func querySQLiteRowsAfterWatermark(ctx context.Context, database *sql.DB, schema sqliteSchema, cursor providerCursor) ([]sqliteSMSRow, error) {
	return querySQLiteRowsWithCondition(
		ctx,
		database,
		schema,
		`("date" > ? OR ("date" = ? AND "_id" > ?))`,
		[]any{cursor.ReceivedAtMillis, cursor.ReceivedAtMillis, cursor.RowID},
	)
}

func querySQLiteRowsWithCondition(ctx context.Context, database *sql.DB, schema sqliteSchema, cursorCondition string, cursorArgs []any) ([]sqliteSMSRow, error) {
	selected := []string{`"_id"`, `"address"`, `"body"`, `"date"`}
	if schema.columns["sub_id"] {
		selected = append(selected, `"sub_id"`)
	}
	if schema.columns["subscription_id"] {
		selected = append(selected, `"subscription_id"`)
	}
	if schema.columns["sim_id"] {
		selected = append(selected, `"sim_id"`)
	}
	query := "SELECT " + strings.Join(selected, ", ") + " FROM \"sms\""
	conditions := make([]string, 0, 3)
	args := append([]any(nil), cursorArgs...)
	if cursorCondition != "" {
		conditions = append(conditions, cursorCondition)
	}
	if schema.columns["type"] {
		conditions = append(conditions, `"type" = 1`)
	}
	if schema.columns["deleted"] {
		conditions = append(conditions, `"deleted" = 0`)
	}
	if len(conditions) > 0 {
		query += " WHERE " + strings.Join(conditions, " AND ")
	}
	query += " ORDER BY \"date\" ASC, \"_id\" ASC"
	rows, err := database.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make([]sqliteSMSRow, 0)
	for rows.Next() {
		values := make([]any, len(selected))
		pointers := make([]any, len(selected))
		for index := range values {
			pointers[index] = &values[index]
		}
		if err := rows.Scan(pointers...); err != nil {
			return nil, err
		}
		row := sqliteSMSRow{}
		for index, column := range selected {
			name := strings.Trim(column, `"`)
			switch name {
			case "_id":
				row.id, err = sqliteInt64(values[index])
			case "address":
				row.sender, err = sqliteString(values[index])
			case "body":
				row.body, err = sqliteString(values[index])
			case "date":
				row.dateMillis, err = sqliteInt64(values[index])
			case "sub_id", "subscription_id":
				value, valueErr := sqliteOptionalInt(values[index])
				if valueErr != nil {
					err = valueErr
				} else if value != nil && row.subscriptionID == nil {
					row.subscriptionID = value
				}
			case "sim_id":
				row.simID, err = sqliteOptionalInt(values[index])
			}
			if err != nil {
				return nil, err
			}
		}
		if row.id < 0 || row.dateMillis < 0 {
			return nil, fmt.Errorf("invalid sqlite sms row")
		}
		result = append(result, row)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return result, nil
}

func sqliteInt64(value any) (int64, error) {
	switch current := value.(type) {
	case int64:
		return current, nil
	case int:
		return int64(current), nil
	case []byte:
		return strconv.ParseInt(string(current), 10, 64)
	case string:
		return strconv.ParseInt(current, 10, 64)
	default:
		return 0, fmt.Errorf("invalid sqlite integer")
	}
}

func sqliteString(value any) (string, error) {
	switch current := value.(type) {
	case string:
		return current, nil
	case []byte:
		return string(current), nil
	default:
		return "", fmt.Errorf("invalid sqlite text")
	}
}

func sqliteOptionalInt(value any) (*int, error) {
	if value == nil {
		return nil, nil
	}
	parsed, err := sqliteInt64(value)
	if err != nil || parsed > int64(^uint(0)>>1) {
		return nil, fmt.Errorf("invalid sqlite optional integer")
	}
	// MIUI and some other ROMs use -1 when the subscription column is
	// unavailable, while exposing the usable card identity through sim_id.
	if parsed < 0 {
		return nil, nil
	}
	result := int(parsed)
	return &result, nil
}

func sqliteRowEvent(row sqliteSMSRow, profileSets ...[]simProfile) smsEvent {
	event := smsEvent{
		SchemaVersion:   1,
		ClientMessageID: eventID("provider", row.id, row.dateMillis),
		ReceivedAt:      time.UnixMilli(row.dateMillis).UTC().Format(time.RFC3339Nano),
		Sender:          row.sender,
		Body:            row.body,
		Source:          "root_module",
		Metadata: eventMetadata{
			SourceMessageID: strconv.FormatInt(row.id, 10),
			SubscriptionID:  row.subscriptionID,
			SIMID:           row.simID,
		},
	}
	if len(profileSets) > 0 {
		if snapshot, ok := chooseSIMSnapshot(profileSets[0], row.subscriptionID, row.simID, nil); ok {
			event.SIMSlot = snapshot.SlotIndex
			event.Metadata.SubscriptionID = snapshot.SubscriptionID
			event.Metadata.SIMID = snapshot.SIMID
			event.Metadata.PhoneNumber = snapshot.PhoneNumber
			event.Metadata.CarrierName = snapshot.CarrierName
		}
	}
	return event
}
