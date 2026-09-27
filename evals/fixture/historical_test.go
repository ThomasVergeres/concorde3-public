package main

import (
	"encoding/json"
	"github.com/ThomasVergeres/concorde3-public/core"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestHistoricalBoundaryPreservesMeaningAndRejectsWrongCopy(t *testing.T) {
	source := t.TempDir()
	target := t.TempDir()
	s := core.Open(target)
	if err := s.Init("A broad persistent undertaking"); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "fixture", "historical program handle", func(st *core.State) error {
		st.Programs["inbox"] = core.Program{ID: "inbox", Command: []string{"/bin/true"}, Pursuit: "purpose", Enabled: true, Status: "running", Process: core.Process{PID: 123456}}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	before, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(target, ".concorde2/state.json"))
	manifest := map[string]any{"source": source, "target": target, "state_sha256": "wrong", "sequence": before.Seq}
	write := func() {
		b, _ := json.Marshal(manifest)
		if e := os.WriteFile(filepath.Join(target, "historical-boundary.json"), b, 0600); e != nil {
			t.Fatal(e)
		}
	}
	write()
	if historicalBoundary(target) == nil {
		t.Fatal("wrong hash accepted")
	}
	manifest["state_sha256"] = core.Digest(raw)
	manifest["source"] = target
	write()
	if historicalBoundary(target) == nil {
		t.Fatal("source accepted as target")
	}
	manifest["source"] = source
	write()
	if err = historicalBoundary(target); err != nil {
		t.Fatal(err)
	}
	after, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	if after.Programs["inbox"].Process != (core.Process{}) || !after.Programs["inbox"].Enabled || after.Programs["inbox"].Status != "stopped" {
		t.Fatal("unsafe copied process disposition")
	}
	for key, old := range map[string]any{"config": before.Config, "nodes": before.Nodes, "items": before.Items, "edges": before.Edges, "wakes": before.Wakes} {
		values := map[string]any{"config": after.Config, "nodes": after.Nodes, "items": after.Items, "edges": after.Edges, "wakes": after.Wakes}
		a, _ := json.Marshal(old)
		b, _ := json.Marshal(values[key])
		if string(a) != string(b) {
			t.Fatal("meaning changed", key)
		}
	}
}

func TestRectificationBoundaryRecoversOnlyCopiedState(t *testing.T) {
	source, target := t.TempDir(), t.TempDir()
	s := core.Open(target)
	if err := s.Init("A continuing undertaking"); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	if err := s.Update("operator", "fixture", "historical rectification", func(st *core.State) error {
		st.Mode = "running"
		st.Activations["act.original"] = core.Activation{ID: "act.original", Pursuit: "purpose", Phase: "rectification", Status: "running",
			Started: now.Add(-time.Minute), Deadline: now.Add(time.Hour), WorkSummary: "Verified work, open broader goal",
			Session: "old-session", Process: core.Process{PID: 123456}, Config: st.Config}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	before, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(target, ".concorde2/state.json"))
	manifest := map[string]any{"source": source, "target": target, "state_sha256": core.Digest(raw), "sequence": before.Seq, "activation": "act.original"}
	b, _ := json.Marshal(manifest)
	if err = os.WriteFile(filepath.Join(target, "historical-boundary.json"), b, 0600); err != nil {
		t.Fatal(err)
	}
	if err = rectificationBoundary(target); err != nil {
		t.Fatal(err)
	}
	after, err := s.Read()
	if err != nil {
		t.Fatal(err)
	}
	a := after.Activations["act.original"]
	if after.Mode != "paused" || a.Phase != "rectification_pending" || a.Status != "failed" || a.Session != "" || a.Process != (core.Process{}) ||
		a.WorkSummary != before.Activations["act.original"].WorkSummary || a.RetryAt.After(time.Now()) {
		t.Fatalf("invalid isolated recovery state: %+v", a)
	}
	if after.Items["purpose"].Text != before.Items["purpose"].Text || !reflect.DeepEqual(after.Config, before.Config) {
		t.Fatal("the recovery boundary changed goal or config")
	}
	if err = rectificationBoundary(target); err == nil {
		t.Fatal("same historical boundary accepted twice")
	}
}
