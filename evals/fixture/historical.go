package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ThomasVergeres/concorde3-public/core"
)

// Experiment-only normalization of a verified historical copy. Never signal old
// PIDs: they can belong to the still-running source. No graph or clock rewriting.
func historicalBoundary(dir string) error {
	dir, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(filepath.Join(dir, "historical-boundary.json"))
	if err != nil {
		return err
	}
	var m struct {
		Source    string `json:"source"`
		Target    string `json:"target"`
		StateHash string `json:"state_sha256"`
		Sequence  int64  `json:"sequence"`
	}
	if err = json.Unmarshal(raw, &m); err != nil {
		return err
	}
	source, err := filepath.Abs(m.Source)
	if err != nil {
		return err
	}
	if m.Target != dir || source == dir || strings.HasPrefix(dir, source+string(os.PathSeparator)) || strings.HasPrefix(source, dir+string(os.PathSeparator)) {
		return fmt.Errorf("independent exact target required")
	}
	for _, p := range []string{dir, source} {
		resolved, e := filepath.EvalSymlinks(p)
		if e != nil || resolved != p {
			return fmt.Errorf("real disjoint roots required")
		}
	}
	state, err := os.ReadFile(filepath.Join(dir, ".concorde2", "state.json"))
	if err != nil {
		return err
	}
	if core.Digest(state) != m.StateHash {
		return fmt.Errorf("historical state hash mismatch")
	}
	return core.Open(dir).Update("operator", "experiment.historical_boundary", "Isolated historical restart; original graph, dates, allocations and history retained. Process RAM is unavailable; clear copied handles without signaling source. Single-decision diagnostic, not exact process replay.", func(st *core.State) error {
		if st.Seq != m.Sequence {
			return fmt.Errorf("historical sequence mismatch")
		}
		for _, a := range st.Activations {
			if a.Status == "running" {
				return fmt.Errorf("pre-admission boundary required")
			}
		}
		st.Mode = "paused"
		for id, a := range st.Activations {
			a.Process = core.Process{}
			st.Activations[id] = a
		}
		for id, p := range st.Programs {
			p.Process = core.Process{}
			p.Status = "stopped"
			st.Programs[id] = p
		}
		return nil
	})
}

// A fresh-session continuation from a copied, already-entered rectification
// turn. The original work, graph and evidence are retained; the old session
// conversation and process RAM are unavailable. Never touches the source.
func rectificationBoundary(dir string) error {
	dir, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(filepath.Join(dir, "historical-boundary.json"))
	if err != nil {
		return err
	}
	var m struct {
		Source    string `json:"source"`
		Target    string `json:"target"`
		StateHash string `json:"state_sha256"`
		Sequence  int64  `json:"sequence"`
		Actor     string `json:"activation"`
	}
	if err = json.Unmarshal(raw, &m); err != nil {
		return err
	}
	source, err := filepath.Abs(m.Source)
	if err != nil {
		return err
	}
	if m.Target != dir || source == dir || strings.HasPrefix(dir, source+string(os.PathSeparator)) || strings.HasPrefix(source, dir+string(os.PathSeparator)) {
		return fmt.Errorf("independent exact target required")
	}
	for _, p := range []string{dir, source} {
		resolved, e := filepath.EvalSymlinks(p)
		if e != nil || resolved != p {
			return fmt.Errorf("real disjoint roots required")
		}
	}
	state, err := os.ReadFile(filepath.Join(dir, ".concorde2", "state.json"))
	if err != nil {
		return err
	}
	if core.Digest(state) != m.StateHash {
		return fmt.Errorf("historical state hash mismatch")
	}
	return core.Open(dir).Update("operator", "experiment.rectification_boundary", "Isolated historical rectification restart; work and graph retained, previous conversation/process unavailable", func(st *core.State) error {
		if st.Seq != m.Sequence || st.Activations[m.Actor].ID != m.Actor || m.Actor == "" {
			return fmt.Errorf("unexpected historical rectification boundary")
		}
		for id, a := range st.Activations {
			if a.Status == "running" && id != m.Actor {
				return fmt.Errorf("other in-flight activation %s", id)
			}
		}
		a := st.Activations[m.Actor]
		if a.Status != "running" || a.Phase != "rectification" || a.WorkSummary == "" {
			return fmt.Errorf("original rectification work summary required")
		}
		a.Status = "failed"
		a.Phase = "rectification_pending"
		a.Session = ""
		a.Process = core.Process{}
		a.RetryAt = time.Now().UTC().Add(-time.Second)
		st.Activations[m.Actor] = a
		for id, p := range st.Programs {
			p.Process = core.Process{}
			p.Status = "stopped"
			st.Programs[id] = p
		}
		st.Mode = "paused"
		return nil
	})
}
