package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestReaderDiagnosticRoundTripIsBoundedAndRedacted(t *testing.T) {
	stateDir := t.TempDir()
	writeReaderDiagnostic(filepath.Join(stateDir, "reader"), readerDiagnostic{
		Mode: "SQLite", Path: "/data/user/0/com.android.providers.telephony/databases/mmssms.db",
		Source: "自动", MaxID: 15641, Cursor: 15641, Columns: []string{"_id", "sim_id"},
	})
	got, err := readReaderDiagnostic(stateDir)
	if err != nil {
		t.Fatal(err)
	}
	if got.Mode != "SQLite" || got.MaxID != 15641 || got.Cursor != 15641 || len(got.Columns) != 2 {
		t.Fatalf("diagnostic=%+v", got)
	}
	if info, err := os.Stat(readerDiagnosticPath(stateDir)); err != nil {
		t.Fatal(err)
	} else if runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("diagnostic mode=%o", info.Mode().Perm())
	}
}

func TestReaderDiagnosticRejectsUnknownFieldsAndOversizedFile(t *testing.T) {
	stateDir := t.TempDir()
	path := readerDiagnosticPath(stateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(`{"mode":"SQLite","unknown":"x"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := readReaderDiagnostic(stateDir); err == nil {
		t.Fatal("unknown diagnostic field accepted")
	}
	if err := os.WriteFile(path, make([]byte, 4097), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := readReaderDiagnostic(stateDir); err == nil {
		t.Fatal("oversized diagnostic accepted")
	}
}
