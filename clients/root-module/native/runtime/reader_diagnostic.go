package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"time"
)

type readerDiagnostic struct {
	Mode      string   `json:"mode,omitempty"`
	Path      string   `json:"path,omitempty"`
	Source    string   `json:"source,omitempty"`
	MaxID     int64    `json:"max_id,omitempty"`
	Cursor    int64    `json:"cursor,omitempty"`
	Columns   []string `json:"columns,omitempty"`
	ErrorCode string   `json:"error_code,omitempty"`
	UpdatedAt string   `json:"updated_at,omitempty"`
}

func readerDiagnosticPath(stateDir string) string {
	return filepath.Join(stateDir, "reader", "reader.diagnostic.json")
}

func readReaderDiagnostic(stateDir string) (readerDiagnostic, error) {
	path := readerDiagnosticPath(stateDir)
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4096 || runtime.GOOS != "windows" && info.Mode().Perm()&0o077 != 0 {
		return readerDiagnostic{}, errors.New("reader diagnostic unavailable")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return readerDiagnostic{}, err
	}
	var diagnostic readerDiagnostic
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&diagnostic); err != nil || decoder.Decode(&struct{}{}) != io.EOF || diagnostic.Path != "" && len(diagnostic.Path) > 512 || len(diagnostic.ErrorCode) > 64 || len(diagnostic.Columns) > 128 {
		return readerDiagnostic{}, errors.New("reader diagnostic invalid")
	}
	return diagnostic, nil
}

func writeReaderDiagnostic(readerStateDir string, diagnostic readerDiagnostic) {
	diagnostic.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	raw, err := json.Marshal(diagnostic)
	if err != nil {
		return
	}
	_ = writePrivateFileAtomic(filepath.Join(readerStateDir, "reader.diagnostic.json"), append(raw, '\n'))
}
