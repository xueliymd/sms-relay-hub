//go:build !sqlite_fallback

package main

import (
	"context"
	"fmt"
)

type sqliteDatabaseInfo struct {
	Path    string
	MaxID   int64
	Columns []string
	Rows    int64
}

func runSQLiteFallback(context.Context, readOptions, providerCursor) error {
	return fmt.Errorf("database_fallback_unavailable")
}

func discoverSQLiteDatabases(int) []string { return nil }

func validateSQLiteDatabase(context.Context, string) (sqliteDatabaseInfo, error) {
	return sqliteDatabaseInfo{}, fmt.Errorf("database_fallback_unavailable")
}
