package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ThomasVergeres/concorde3-public/core"
)

func TestForkPreservesHistoryAndOriginalState(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "subjects", "test")
	s := core.Open(dir)
	if err := s.Init("Persistent undertaking"); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "freeze", "fixture boundary", func(st *core.State) error { st.Mode = "frozen"; st.Config.Workspace = true; return nil }); err != nil {
		t.Fatal(err)
	}
	before, _ := s.Read()
	source := strings.Repeat("a", 64)
	manifest, _ := json.Marshal(map[string]any{"snapshot_sha256": source, "status": "preparing"})
	if err := os.WriteFile(filepath.Join(root, "fork.json"), manifest, 0600); err != nil {
		t.Fatal(err)
	}
	spec, _ := json.Marshal(map[string]any{"source_manifest": source, "cutoff": time.Now().Add(time.Hour).Unix(), "model": "gpt-5.6-luna", "effort": "xhigh", "starts": 3})
	in, err := os.CreateTemp(t.TempDir(), "input")
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	_, _ = in.Write(spec)
	_, _ = in.Seek(0, 0)
	previous := os.Stdin
	os.Stdin = in
	defer func() { os.Stdin = previous }()
	if err = forkInstance(dir); err != nil {
		t.Fatal(err)
	}
	after, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	if after.Mode != "paused" || after.Config.Model != "gpt-5.6-luna" || after.Config.StartsPerHour != 3 || after.Seq != before.Seq+1 {
		t.Fatal("fork configuration/provenance lost")
	}
	if after.Items["purpose"].Text != before.Items["purpose"].Text || len(after.Items) != len(before.Items) {
		t.Fatal("reseeded memory")
	}
	_, _ = in.Seek(0, 0)
	if err = forkInstance(dir); err == nil {
		t.Fatal("reopening nonfrozen copy accepted")
	}
}
