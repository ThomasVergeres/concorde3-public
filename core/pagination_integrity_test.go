package core

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"unicode/utf8"
)

// Inspect the actual JSON transport, not raw Go strings: encoding/json replaces
// invalid UTF-8, which can hide a split-codepoint corruption in an in-memory test.
func collectTextPages(t *testing.T, s *Store, tool string, args map[string]any, key string) string {
	t.Helper()
	var text strings.Builder
	offset := 0
	for pages := 0; pages < 20000; pages++ {
		args["offset"] = offset
		value, err := s.Call(context.Background(), "operator", tool, Marshal(args))
		if err != nil {
			t.Fatal(tool, err)
		}
		raw := Marshal(value)
		var page map[string]json.RawMessage
		if err := json.Unmarshal(raw, &page); err != nil {
			t.Fatal(err)
		}
		var chunk string
		var next int
		if err := json.Unmarshal(page[key], &chunk); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(page["next_offset"], &next); err != nil {
			t.Fatal(err)
		}
		if !utf8.ValidString(value.(map[string]any)[key].(string)) {
			t.Fatal("page cuts through UTF-8 codepoint")
		}
		text.WriteString(chunk)
		if next < 0 {
			return text.String()
		}
		if next != offset+len(chunk) || next <= offset {
			t.Fatal("byte offset skipped, repeated or stalled", offset, next, len(chunk))
		}
		offset = next
	}
	t.Fatal("pagination did not terminate")
	return ""
}

func TestUTF8RecordAndEventPagesPreserveEvidence(t *testing.T) {
	s := fixture(t)
	i := itemFixture("international", strings.Repeat("é 東京 🧭 e\u0301\n", 40))
	if err := s.Mutate("operator", "retain exact international customer evidence", Batch{Items: []ItemChange{{Item: i}}}); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	for _, limit := range []int{4, 7, 16, 23} {
		got := collectTextPages(t, s, "record", map[string]any{"section": "items", "id": i.ID, "limit": limit}, "text")
		if got != string(Marshal(st.Items[i.ID])) {
			t.Fatal("record changed across JSON pages")
		}
		got = collectTextPages(t, s, "event", map[string]any{"sequence": st.Seq, "limit": limit}, "text")
		events, err := s.History("", int(st.Seq)-1, 1)
		if err != nil || len(events) != 1 || got != string(Marshal(events[0])) {
			t.Fatal("immutable event changed across JSON pages", err)
		}
	}
}

func TestArtifactPagesPreserveUTF8AndRespectLimits(t *testing.T) {
	s := fixture(t)
	if err := os.MkdirAll(filepath.Join(s.Dir, "artifacts"), 0700); err != nil {
		t.Fatal(err)
	}
	content := strings.Repeat("東京é🧭", 3000)
	if err := os.WriteFile(filepath.Join(s.Dir, "artifacts", "source.txt"), []byte(content), 0600); err != nil {
		t.Fatal(err)
	}
	args := map[string]any{"path": "source.txt", "limit": 23}
	first, err := s.Call(context.Background(), "operator", "artifact", Marshal(args))
	if err != nil {
		t.Fatal(err)
	}
	if len(first.(map[string]any)["content"].(string)) > 23 {
		t.Fatal("artifact ignored requested byte limit")
	}
	if got := collectTextPages(t, s, "artifact", args, "content"); got != content {
		t.Fatal("artifact changed across JSON pages")
	}
}

func TestEscapedArtifactRemainsRetrievableWithinResponseBudget(t *testing.T) {
	s := fixture(t)
	if err := os.MkdirAll(filepath.Join(s.Dir, "artifacts"), 0700); err != nil {
		t.Fatal(err)
	}
	content := strings.Repeat("\x00<&>\t\n", 4000)
	if err := os.WriteFile(filepath.Join(s.Dir, "artifacts", "source.txt"), []byte(content), 0600); err != nil {
		t.Fatal(err)
	}
	if got := collectTextPages(t, s, "artifact", map[string]any{"path": "source.txt"}, "content"); got != content {
		t.Fatal("escaped artifact could not be reconstructed")
	}
}

func TestTextPagesRejectInvalidBytesAndNonBoundaryOffsets(t *testing.T) {
	s := fixture(t)
	if err := os.MkdirAll(filepath.Join(s.Dir, "artifacts"), 0700); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		data          []byte
		offset, limit int
		want          string
	}{
		{[]byte{0xff, 0, 1}, 0, 4, "not valid UTF-8"},
		{[]byte("🧭x"), 1, 4, "not a UTF-8 character boundary"},
		{[]byte("🧭x"), 0, 1, "retry with limit >= 4"},
	} {
		if err := os.WriteFile(filepath.Join(s.Dir, "artifacts", "source.txt"), tc.data, 0600); err != nil {
			t.Fatal(err)
		}
		_, err := s.Call(context.Background(), "operator", "artifact", Marshal(map[string]any{"path": "source.txt", "offset": tc.offset, "limit": tc.limit}))
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Fatal("invalid source/cursor silently repaired", err)
		}
	}
	for _, name := range []string{"directory", "pipe"} {
		path := filepath.Join(s.Dir, "artifacts", name)
		var err error
		if name == "pipe" {
			err = syscall.Mkfifo(path, 0600)
		} else {
			err = os.Mkdir(path, 0700)
		}
		if err != nil {
			t.Fatal(err)
		}
		if _, err = s.Call(context.Background(), "operator", "artifact", Marshal(map[string]any{"path": name})); err == nil || !strings.Contains(err.Error(), "regular UTF-8 text file") {
			t.Fatal("nonregular artifact accepted", err)
		}
	}
}

func TestUnicodeEvidenceSurvivesRectificationAndRestart(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	i := itemFixture("customer-evidence", "Montréal: 東京支店 🧭 — confirmed, not inferred")
	i.Sources = []Source{{Ref: "artifacts/entrée.txt", ObservedAt: s.Now()}}
	if err := s.Mutate(a.ID, "customer evidence encountered", Batch{Items: []ItemChange{{Item: i}}}); err != nil {
		t.Fatal(err)
	}
	if err := s.BeginRectification(a.ID, "Preserve exact customer evidence", "test-session"); err != nil {
		t.Fatal(err)
	}
	read := func(store *Store) Item {
		text := collectTextPages(t, store, "record", map[string]any{"section": "items", "id": i.ID, "limit": 7}, "text")
		var item Item
		if err := json.Unmarshal([]byte(text), &item); err != nil {
			t.Fatal(err)
		}
		return item
	}
	first := read(s)
	complete(t, s, a, "continue")
	r := Open(s.Dir)
	r.Now = s.Now
	b, _, err := r.Admit()
	if err != nil {
		t.Fatal(err)
	}
	got := read(r)
	if got.Text != i.Text || got.Sources[0] != i.Sources[0] || got.Actor != a.ID || string(Marshal(first)) != string(Marshal(got)) {
		t.Fatal("evidence or provenance changed across rectification/restart", got)
	}
	complete(t, r, b, "wait")
}

func TestTruncatedPageCannotReturnANonAdvancingCursor(t *testing.T) {
	// A file may shrink after stat but before ReadAt. An empty page that claims
	// more bytes must not send a consumer into an endless same-offset loop.
	if _, err := textPage(nil, 20, 23, 1024, "content", true, map[string]any{}); err == nil {
		t.Fatal("empty partial source returned a non-advancing cursor")
	}
}
