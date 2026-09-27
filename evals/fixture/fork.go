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

// Lab-only, provenance-bearing reopening of a copied quiescent snapshot. Never
// exposes a production unfreeze command and never resumes a source or old process.
func forkInstance(dir string) error {
	var spec struct {
		Cutoff         float64  `json:"cutoff"`
		Model          string   `json:"model"`
		Effort         string   `json:"effort"`
		Starts         int      `json:"starts"`
		Source         string   `json:"source_manifest"`
		ManifestPath   string   `json:"manifest_path,omitempty"`
		ResumePrograms []string `json:"resume_programs"`
	}
	d := json.NewDecoder(os.Stdin)
	d.DisallowUnknownFields()
	if err := d.Decode(&spec); err != nil {
		return err
	}
	manifestPath := spec.ManifestPath
	if manifestPath == "" {
		manifestPath = filepath.Join(dir, "..", "..", "fork.json")
	}
	b, err := os.ReadFile(manifestPath)
	if err != nil {
		return fmt.Errorf("fork manifest required: %w", err)
	}
	var manifest struct {
		Source string `json:"snapshot_sha256"`
		Status string `json:"status"`
	}
	if err = json.Unmarshal(b, &manifest); err != nil {
		return err
	}
	digest, decodeErr := hex.DecodeString(spec.Source)
	if manifest.Status != "preparing" || decodeErr != nil || len(digest) != 32 || manifest.Source != spec.Source {
		return fmt.Errorf("explicit preparing fork provenance required")
	}
	cutoff := time.Unix(int64(spec.Cutoff), 0).UTC()
	if !cutoff.After(time.Now()) || cutoff.After(time.Now().Add(6*time.Hour)) || spec.Starts < 1 || spec.Starts > 6 {
		return fmt.Errorf("bounded future fork required")
	}
	if !((spec.Model == "gpt-5.6-luna" && spec.Effort == "xhigh") || (spec.Model == "gpt-5.6-terra" && spec.Effort == "medium")) {
		return fmt.Errorf("unsupported fork profile")
	}
	s := core.Open(dir)
	return s.Update("operator", "experiment.isolated_fork", "Isolated lived-state continuation from "+spec.Source+"; new execution window/model, original dates retained; not historical exact replay", func(st *core.State) error {
		if st.Mode != "frozen" {
			return fmt.Errorf("quiescent frozen source required")
		}
		if len(st.Programs) > 0 && spec.ResumePrograms == nil {
			return fmt.Errorf("program-bearing source requires explicit resume_programs review (empty list keeps stopped)")
		}
		for _, a := range st.Activations {
			if a.Status == "running" || a.Process != (core.Process{}) {
				return fmt.Errorf("in-flight source or retained activation process handle")
			}
		}
		for _, p := range st.Programs {
			if p.Enabled || p.Status != "stopped" || p.Process != (core.Process{}) {
				return fmt.Errorf("program %s must be disabled/stopped with no process handle", p.ID)
			}
		}
		selected := map[string]bool{}
		for _, id := range spec.ResumePrograms {
			if _, ok := st.Programs[id]; !ok || selected[id] {
				return fmt.Errorf("unknown or duplicate resume_programs ID %s", id)
			}
			selected[id] = true
		}
		for id := range selected {
			p := st.Programs[id]
			p.Enabled = true
			st.Programs[id] = p
		}
		st.Mode = "paused"
		st.Config.FreezeAt = cutoff
		st.Config.Model, st.Config.Effort = spec.Model, spec.Effort
		st.Config.StartsPerHour, st.Config.DeadlineSeconds = spec.Starts, 300
		st.Config.Concurrency, st.Config.ExternalSandbox = 1, true
		return nil
	})
}
