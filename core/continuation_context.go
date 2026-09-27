package core

import (
	"sort"
	"time"
	"unicode/utf8"
)

// Views are bounded projections, not a second memory store. Full source records
// remain accessible through record(section=activations, id=...). Reported work
// is not independently verified merely because it appears in this view.
type ContinuationView struct {
	Activation string     `json:"activation"`
	Intention  string     `json:"intention"`
	At         time.Time  `json:"at"`
	Decision   Completion `json:"decision"`
	Truncated  bool       `json:"truncated,omitempty"`
	Basis      string     `json:"basis"`
}

type ActivationView struct {
	Activation      string    `json:"activation"`
	Intention       string    `json:"intention"`
	Started         time.Time `json:"started"`
	Finished        time.Time `json:"finished"`
	Status          string    `json:"status"`
	Activity        string    `json:"activity,omitempty"`
	ReportedOutcome string    `json:"reported_outcome"`
	Continuation    string    `json:"continuation,omitempty"`
	Reason          string    `json:"reason,omitempty"`
	Usage           Usage     `json:"usage"`
	ReceiptCount    int       `json:"receipt_count"`
	Receipts        []string  `json:"receipt_refs,omitempty"`
	Truncated       bool      `json:"truncated,omitempty"`
}

func clipContext(text string, limit int, truncated *bool) string {
	if len(text) <= limit {
		return text
	}
	*truncated = true
	for limit > 0 && !utf8.RuneStart(text[limit]) {
		limit--
	}
	return text[:limit] + "…"
}

func previousActivations(st State, a Activation) []Activation {
	var result []Activation
	for _, x := range st.Activations {
		if x.ID != a.ID && !x.Finished.IsZero() && !x.Finished.After(a.Started) {
			result = append(result, x)
		}
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Finished.Equal(result[j].Finished) {
			return result[i].ID > result[j].ID
		}
		return result[i].Finished.After(result[j].Finished)
	})
	return result
}

func previousCompletion(st State, a Activation) *Activation {
	for _, x := range previousActivations(st, a) {
		if x.Pursuit == a.Pursuit && x.Completion != nil && x.Status == "completed" {
			return &x
		}
	}
	return nil
}

func addContinuity(st State, a Activation, p *Packet) {
	base := len(Marshal(p))
	limit := min(st.Config.ContextBytes-512, base+st.Config.ContextBytes/3)
	if x := previousCompletion(st, a); x != nil {
		v := ContinuationView{Activation: x.ID, Intention: x.Pursuit, At: x.Finished,
			Decision: *x.Completion, Basis: "Prior self-authored decision, not an instruction or proof of resolution. Reassess against current evidence; full activation record remains available."}
		v.Decision.Reason = clipContext(v.Decision.Reason, 700, &v.Truncated)
		v.Decision.Coverage = clipContext(v.Decision.Coverage, 700, &v.Truncated)
		if len(v.Decision.Outstanding) > 12 {
			v.Decision.Outstanding = v.Decision.Outstanding[:12]
			v.Truncated = true
		}
		if len(v.Decision.Considered) > 8 {
			v.Decision.Considered = v.Decision.Considered[:8]
			v.Truncated = true
		}
		p.Handoff = &v
		if len(Marshal(p)) > limit {
			p.Handoff = nil
			p.Omitted++
		}
	}
	// Local work retains its predecessor; whole-self and rectification additionally
	// see a small cross-intention trajectory. There is no automatic evaluation.
	if a.Pursuit != st.Config.Reconsideration && a.Phase != "rectification" {
		return
	}
	for _, x := range previousActivations(st, a) {
		if len(p.Recent) >= 4 {
			p.Omitted++
			break
		}
		v := ActivationView{Activation: x.ID, Intention: x.Pursuit, Started: x.Started, Finished: x.Finished, Status: x.Status, Usage: x.Usage}
		v.Activity = clipContext(x.Activity, 180, &v.Truncated)
		outcome := x.WorkSummary
		if outcome == "" {
			outcome = x.Summary
		}
		v.ReportedOutcome = clipContext(outcome, 500, &v.Truncated)
		if x.Completion != nil {
			v.Continuation = x.Completion.Continuation
			v.Reason = clipContext(x.Completion.Reason, 300, &v.Truncated)
		}
		for _, id := range sortedKeys(st.Receipts) {
			if st.Receipts[id].Actor == x.ID {
				v.ReceiptCount++
				if len(v.Receipts) < 3 {
					v.Receipts = append(v.Receipts, id)
				} else {
					v.Truncated = true
				}
			}
		}
		p.Recent = append(p.Recent, v)
		if len(Marshal(p)) > limit {
			p.Recent = p.Recent[:len(p.Recent)-1]
			p.Omitted++
			break
		}
	}
}

func addPendingConsequences(st State, a Activation, p *Packet) {
	// Reserve a bounded slice of the remaining packet, even when required practices
	// already exceed the old fixed 75% watermark. Optional history comes later.
	limit := min(st.Config.ContextBytes-512, len(Marshal(p))+st.Config.ContextBytes/8)
	// Only the most recent completion supplies outstanding status. Historical
	// decisions must not resurrect a matter later resolved or deliberately dropped.
	ids := []string{}
	if x := previousCompletion(st, a); x != nil {
		ids = append(ids, x.Completion.Outstanding...)
	}
	ids = append(ids, sortedKeys(st.Consequences)...)
	seen := map[string]bool{}
	for n, id := range ids {
		if seen[id] {
			continue
		}
		seen[id] = true
		c, ok := st.Consequences[id]
		if !ok {
			continue
		}
		explicit := n < len(ids)-len(st.Consequences)
		eligible := explicit
		for _, target := range c.Targets {
			if target == a.Pursuit && c.Acknowledgments[target] == "" {
				eligible = true
			}
		}
		if !eligible {
			continue
		}
		p.Consequences = append(p.Consequences, c)
		if len(Marshal(p)) > limit {
			p.Consequences = p.Consequences[:len(p.Consequences)-1]
			p.Omitted++
		}
	}
}
