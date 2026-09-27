package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/ThomasVergeres/concorde3-public/core"
)

// admissionWitness admits and immediately freezes a disposable prepared world
// fork. It never runs a harness or changes intention allocation/deferral. This
// produces a canceled synthetic admission, not a completed cognitive activation.
func admissionWitness(dir string) (proof map[string]any, err error) {
	dir, err = filepath.Abs(dir)
	if err != nil {
		return nil, err
	}
	var manifest struct {
		Status   string                     `json:"status"`
		Source   string                     `json:"snapshot_sha256"`
		Subjects map[string]json.RawMessage `json:"subjects"`
	}
	b, err := os.ReadFile(filepath.Join(dir, "..", "..", "fork.json"))
	if err != nil {
		return nil, err
	}
	if err = json.Unmarshal(b, &manifest); err != nil {
		return nil, err
	}
	hash, decodeErr := hex.DecodeString(manifest.Source)
	_, named := manifest.Subjects[filepath.Base(dir)]
	if filepath.Base(filepath.Dir(dir)) != "subjects" || manifest.Status != "prepared" || !named || decodeErr != nil || len(hash) != 32 {
		return nil, fmt.Errorf("explicit prepared disposable world fork required")
	}
	s := core.Open(dir)
	before, err := s.Read()
	if err != nil {
		return nil, err
	}
	if before.Mode != "paused" || len(before.Programs) != 0 || !before.Config.FreezeAt.After(time.Now()) {
		return nil, fmt.Errorf("paused future-bounded copy without programs required")
	}
	for _, a := range before.Activations {
		if a.Status == "running" || a.Process != (core.Process{}) {
			return nil, fmt.Errorf("in-flight copy is not a witness")
		}
	}
	defer func() {
		if stopErr := s.Freeze("No-model admission witness complete; never reuse as a behavioral donor"); stopErr != nil && err == nil {
			err = stopErr
		}
	}()
	if err = s.SetMode("running"); err != nil {
		return nil, err
	}
	a, p, err := s.Admit()
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"meaning":         "Native admission only; no harness/model; canceled by immediate freeze",
		"source_manifest": manifest.Source, "before_sequence": before.Seq,
		"activation": a.ID, "intention": a.Pursuit, "phase": a.Phase,
		"reason": a.Reason, "context_hash": a.ContextHash,
		"context_bytes": len(core.Marshal(p)), "starts_remaining": p.StartsRemaining,
		"started": a.Started, "deadline": a.Deadline, "model": a.Config.Model, "effort": a.Config.Effort,
		"before_attention": before.Portfolio(), "model_calls": 0,
	}, nil
}
