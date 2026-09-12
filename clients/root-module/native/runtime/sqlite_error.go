package main

type sqliteFallbackError struct {
	completed bool
	err       error
}

func (failure *sqliteFallbackError) Error() string {
	if failure.err == nil {
		return "database fallback failed"
	}
	return failure.err.Error()
}

func (failure *sqliteFallbackError) Unwrap() error { return failure.err }
