package core

import (
	"fmt"
	"math"
	"os"
	"sort"
	"time"
)

type ContextItem struct {
	Item Item   `json:"item"`
	Why  string `json:"why"`
}

// AttentionLandscape is a bounded projection of the actual allocation, not a
// second scheduler or an instruction to keep every funded intention busy.
type AttentionLandscape struct {
	FundedCount int       `json:"funded_count"`
	Intentions  []Pursuit `json:"intentions"`
	Omitted     int       `json:"omitted"`
}
type Packet struct {
	Identity         string              `json:"identity"`
	Instance         string              `json:"instance"`
	Seq              int64               `json:"sequence"`
	Activation       string              `json:"activation"`
	Pursuit          Pursuit             `json:"attention"`
	Landscape        *AttentionLandscape `json:"attention_landscape,omitempty"`
	Phase            string              `json:"phase"`
	At               time.Time           `json:"at"`
	Deadline         time.Time           `json:"deadline"`
	FreezeAt         time.Time           `json:"experiment_freeze_at,omitempty"`
	FreezeScope      string              `json:"freeze_scope,omitempty"`
	BudgetRefillAt   time.Time           `json:"next_budget_refill_at,omitempty"`
	StartsRemaining  int                 `json:"starts_remaining"`
	Runway           AttentionRunway     `json:"attention_runway"`
	Reason           string              `json:"selection_reason"`
	Items            []ContextItem       `json:"items"`
	Edges            []Edge              `json:"edges"`
	Consequences     []Consequence       `json:"consequences"`
	Handoff          *ContinuationView   `json:"previous_continuation,omitempty"`
	Recent           []ActivationView    `json:"recent_trajectory,omitempty"`
	Wakes            []Wake              `json:"observations"`
	ObservationBatch *ObservationBatch   `json:"observation_batch,omitempty"`
	Live             []string            `json:"live_intentions"`
	Programs         []Program           `json:"programs"`
	Changed          []string            `json:"changed_records"`
	Omitted          int                 `json:"omitted"`
	Guidance         string              `json:"guidance"`
	Error            string              `json:"error,omitempty"`
	Semantic         string              `json:"semantic"`
}

func used(st State, now time.Time) (int, map[string]bool) {
	n := 0
	live := map[string]bool{}
	for _, a := range st.Activations {
		if a.Started.After(now.Add(-time.Hour)) {
			n++
		}
		if a.Status == "running" {
			live[a.Pursuit] = true
		}
	}
	return n, live
}

// Waiting defers an active commitment; it does not erase its claim on attention.
// Explicit next_at wins for local intentions. For the configured whole-self
// intention, it may request an earlier return but cannot postpone the protected
// reconsideration clock. Dormant is event/timer-only; stopped ends admission.
// This clock creates eligibility, never extra capacity or proof of resolution.
func reconsiderAt(st State, p Pursuit) time.Time {
	i := st.Items[p.ID]
	if p.Status != "waiting" || p.Share <= 0 || i.Status == "attained" || i.Status == "abandoned" || i.Status == "retired" {
		return time.Time{}
	}
	if !p.NextAt.IsZero() && p.ID != st.Config.Reconsideration {
		return p.NextAt
	}
	seconds := st.Config.DeferralSeconds
	if seconds == 0 {
		seconds = 600
	}
	if p.ID == st.Config.Reconsideration {
		seconds = st.Config.ReconsiderSeconds
		if seconds == 0 {
			seconds = 1800
		}
	}
	due := deferralAnchor(st, i).Add(time.Duration(seconds) * time.Second)
	if !p.NextAt.IsZero() && p.NextAt.Before(due) {
		return p.NextAt
	}
	return due
}

func deferralAnchor(st State, i Item) time.Time {
	anchor := i.Attention.DeferredAt
	if anchor.IsZero() {
		// Read-compatible old state. Do not rewrite history on load. Once this
		// intention is reconsidered, completion persists a dedicated anchor.
		anchor = i.UpdatedAt
		var last time.Time
		for _, a := range st.Activations {
			if a.Pursuit != i.ID {
				continue
			}
			at := a.Finished
			if at.IsZero() {
				at = a.Started
			}
			if at.After(last) {
				last = at
			}
		}
		if !last.IsZero() {
			anchor = last
		}
	}
	return anchor
}
func ready(st State, p Pursuit, now time.Time) (bool, bool) {
	if p.Share == 0 {
		return false, false
	}
	for _, a := range st.Activations {
		if a.Pursuit == p.ID && a.Phase == "rectification_pending" {
			return !a.RetryAt.After(now), false
		}
	}
	i := st.Items[p.ID]
	if p.Status == "stopped" || i.Status == "attained" || i.Status == "abandoned" || i.Status == "retired" {
		return false, false
	}
	urgent := false
	for _, t := range st.Timers {
		if t.Active && t.Pursuit == p.ID && !t.Due.After(now) {
			urgent = true
		}
	}
	for _, w := range st.Wakes {
		if w.Pursuit == p.ID && w.ConsumedBy == "" && !w.At.After(now) {
			urgent = true
		}
	}
	due := reconsiderAt(st, p)
	return urgent || (p.Status == "ready" && !p.NextAt.After(now)) || (!due.IsZero() && !due.After(now)), urgent
}
func selectPursuit(st *State, now time.Time) (Pursuit, string, error) {
	ps := st.Portfolio()
	ids := []string{}
	total := 0.0
	urg := map[string]bool{}
	_, live := used(*st, now)
	for id, p := range ps {
		r, u := ready(*st, p, now)
		if r && !live[id] {
			ids = append(ids, id)
			total += p.Share
			urg[id] = u
		} else {
			st.Credits[id] = 0
			st.Waits[id] = 0
		}
	}
	if len(ids) == 0 {
		return Pursuit{}, "", fmt.Errorf("idle: no ready intention")
	}
	sort.Strings(ids)
	best := ""
	oldest := ""
	for _, id := range ids {
		st.Credits[id] = min(2, st.Credits[id]+ps[id].Share/total)
		if best == "" || st.Credits[id] > st.Credits[best] {
			best = id
		}
		st.Waits[id]++
		// Keep an arbitrarily large (or +Inf) threshold in floating point.
		// Converting it to int can wrap and grant tiny shares immediate priority.
		if float64(st.Waits[id]) >= math.Ceil(2*total/ps[id].Share) && (oldest == "" || st.Waits[id] > st.Waits[oldest]) {
			oldest = id
		}
	}
	reason := "weighted service deficit"
	if oldest != "" {
		best = oldest
		reason = "waiting-time fairness bound"
	}
	if oldest == "" && st.Starts-st.LastUrgent >= 4 && best != st.Config.Reconsideration {
		for _, id := range ids {
			if urg[id] && st.Credits[id] >= 0 {
				best = id
				reason = "bounded observation/timer urgency"
				st.LastUrgent = st.Starts
				break
			}
		}
	}
	st.Credits[best]--
	st.Waits[best] = 0
	if p := ps[best]; p.Status == "waiting" && !urg[best] {
		if best == st.Config.Reconsideration && !p.ReconsiderAt.After(now) && (p.NextAt.IsZero() || p.ReconsiderAt.Before(p.NextAt)) {
			reason += "; whole-self reconsideration due"
		} else if p.NextAt.IsZero() {
			reason += "; deferral expired"
		}
	}
	return ps[best], reason, nil
}
func Assemble(st State, a Activation) Packet {
	return assembleAt(st, a, a.Started)
}

func addAttentionLandscape(st State, a Activation, p *Packet) {
	all := make([]Pursuit, 0)
	for _, intention := range st.Portfolio() {
		if intention.Share > 0 {
			all = append(all, intention)
		}
	}
	sort.Slice(all, func(i, j int) bool {
		priority := func(id string) int {
			if id == st.Config.Reconsideration {
				return 0
			}
			if id == a.Pursuit {
				return 1
			}
			return 2
		}
		if priority(all[i].ID) != priority(all[j].ID) {
			return priority(all[i].ID) < priority(all[j].ID)
		}
		if all[i].Share != all[j].Share {
			return all[i].Share > all[j].Share
		}
		return all[i].ID < all[j].ID
	})
	p.Landscape = &AttentionLandscape{FundedCount: len(all), Intentions: []Pursuit{}, Omitted: len(all)}
	if len(Marshal(p)) > st.Config.ContextBytes-512 {
		p.Landscape = nil
		p.Omitted++
		return
	}
	for _, intention := range all {
		if len(p.Landscape.Intentions) == 8 {
			break
		}
		p.Landscape.Intentions = append(p.Landscape.Intentions, intention)
		p.Landscape.Omitted = len(all) - len(p.Landscape.Intentions)
		if len(Marshal(p)) > st.Config.ContextBytes-512 {
			p.Landscape.Intentions = p.Landscape.Intentions[:len(p.Landscape.Intentions)-1]
			p.Landscape.Omitted = len(all) - len(p.Landscape.Intentions)
			break
		}
	}
	p.Omitted += p.Landscape.Omitted
}

func assembleAt(st State, a Activation, now time.Time) Packet {
	n, _ := used(st, now)
	p := Packet{Identity: st.Config.Identity, Instance: st.Config.ID, Seq: st.Seq, Activation: a.ID, Pursuit: st.Portfolio()[a.Pursuit], Phase: a.Phase, At: a.Started, Deadline: a.Deadline, StartsRemaining: st.Config.StartsPerHour - n, Reason: a.Reason, Items: []ContextItem{}, Edges: []Edge{}, Consequences: []Consequence{}, Wakes: []Wake{}, Live: []string{}, Programs: []Program{}, Semantic: "unavailable: lexical/graph access; no adapter configured", Guidance: "Consult contract as needed for schemas. Memory is fallible. Expand with item/node/search/links/history. Work ends with a concise summary; a separate rectification turn follows. During rectification call phase_complete. No product implementation in rectification. Omitted evidence remains available through tools."}
	if a.Status == "running" && a.Completion == nil {
		p.Guidance += " attention.reconsider_at is current-state eligibility, not a future completion's result: completing wait without next_at restarts its delay at completion. Optional next_at fixes an intended return time; neither guarantees a start. phase_complete returns the committed attention view."
	}
	p.FreezeAt = st.Config.FreezeAt
	if !p.FreezeAt.IsZero() {
		p.FreezeScope = "The configured freeze governs this instance's cognitive activations and managed programs, not independent external services. Their availability, receiving and settlement depend on separate service terms and evidence."
	}
	p.Runway = attentionRunway(st, now)
	if a.Config.WorkReentry {
		p.Guidance += " Optional resume_work requests one separate return from rectification to work before " + workBoundary(a).Format(time.RFC3339) + "; same lease/deadline, then final rectification. Current turn stays state-only until it ends; phase_complete cancels the request. Unavailable in recovery or after one return."
	}
	for _, x := range st.Activations {
		due := x.Started.Add(time.Hour)
		if due.After(now) && (p.BudgetRefillAt.IsZero() || due.Before(p.BudgetRefillAt)) {
			p.BudgetRefillAt = due
		}
	}
	if len(st.Config.EmbeddingCommand) > 0 {
		p.Semantic = "configured; context refresh reports current index availability"
	}
	included := map[string]bool{}
	inactivePractice := st.Config.RectifyPractice
	if a.Phase == "rectification" {
		inactivePractice = st.Config.WorkPractice
	}
	add := func(i Item, why string, mandatory bool) {
		if i.ID == inactivePractice && !mandatory {
			return
		}
		if included[i.ID] || i.ID == "" || i.Status == "retired" {
			return
		}
		p.Items = append(p.Items, ContextItem{i, why})
		if len(Marshal(p)) > st.Config.ContextBytes-512 {
			if mandatory {
				p.Error = "required context exceeds context_bytes; reduce explicit bindings or increase bound"
			} else {
				p.Items = p.Items[:len(p.Items)-1]
				p.Omitted++
				return
			}
		}
		included[i.ID] = true
	}
	for _, id := range st.Config.Global {
		add(st.Items[id], "explicit global binding", true)
	}
	add(st.Items[a.Pursuit], "selected intention", true)
	practice := st.Config.WorkPractice
	if a.Phase == "rectification" {
		practice = st.Config.RectifyPractice
	}
	add(st.Items[practice], "editable phase practice", true)
	// Required bindings keep their authority. Size causal previews against the
	// remaining room before history or optional relevance can consume it.
	addCausalObservations(st, a, now, &p)
	// Continuity is derived from durable records, before optional observations
	// consume the packet. It describes prior choices, never orders this embodiment.
	addContinuity(st, a, &p)
	addPendingConsequences(st, a, &p)
	// Portfolio orientation must not displace predecessor handoff or unresolved
	// consequences. It is a bounded affordance, not the source of continuity.
	addAttentionLandscape(st, a, &p)
	query := st.Items[a.Pursuit].Text + " " + st.Items[a.Pursuit].Approach + " " + a.Activity
	applicable := sortedKeys(st.Items)
	// Scoped norms are high-salience candidates, but applicability is not an
	// unbounded required-context declaration. Required references live in Global.
	sort.SliceStable(applicable, func(i, j int) bool {
		return st.Items[applicable[i]].Kind == "norm" && st.Items[applicable[j]].Kind != "norm"
	})
	for _, id := range applicable {
		i := st.Items[id]
		for _, binding := range i.AppliesTo {
			if binding == "*" || binding == a.Pursuit || containsFold(query, binding) {
				add(i, "applicable context candidate; expand omitted items through tools", false)
				break
			}
		}
	}
	lineage := map[string]bool{a.ID: true}
	for id := a.RecoveryOf; id != "" && !lineage[id]; id = st.Activations[id].RecoveryOf {
		lineage[id] = true
	}
	for _, id := range sortedKeys(st.Programs) {
		p.Programs = append(p.Programs, st.Programs[id])
		if len(Marshal(p)) > st.Config.ContextBytes*4/5 {
			p.Programs = p.Programs[:len(p.Programs)-1]
			p.Omitted++
		}
	}
	for _, id := range sortedKeys(st.Receipts) {
		if lineage[st.Receipts[id].Actor] {
			p.Changed = append(p.Changed, "receipts/"+id)
			if len(Marshal(p)) > st.Config.ContextBytes-512 {
				p.Changed = p.Changed[:len(p.Changed)-1]
				p.Omitted++
			}
		}
	}
	for _, id := range sortedKeys(st.Activations) {
		x := st.Activations[id]
		if x.Status == "running" && id != a.ID {
			p.Live = append(p.Live, x.Pursuit)
			if len(Marshal(p)) > st.Config.ContextBytes-512 {
				p.Live = p.Live[:len(p.Live)-1]
				p.Omitted++
			}
		}
	}
	for _, id := range sortedKeys(st.Items) {
		i := st.Items[id]
		if lineage[i.Actor] {
			add(i, "changed in this activation/recovery", false)
		} else if i.Node == st.Items[a.Pursuit].Node {
			add(i, "local context", false)
		}
	}
	for _, id := range sortedKeys(st.Edges) {
		e := st.Edges[id]
		if included[e.From] || included[e.To] || e.From == p.Pursuit.Node || e.To == p.Pursuit.Node {
			add(st.Items[e.From], "related item", false)
			add(st.Items[e.To], "related item", false)
			p.Edges = append(p.Edges, e)
			if len(Marshal(p)) > st.Config.ContextBytes-512 {
				p.Edges = p.Edges[:len(p.Edges)-1]
				p.Omitted++
			}
		}
	}
	for _, h := range lexicalHits(st, query) {
		add(st.Items[h.ID], "automatic lexical relevance", false)
	}
	if len(Marshal(p)) > st.Config.ContextBytes {
		p.Error = "essential context exceeds configured bound"
	}
	return p
}
func sortedKeys[T any](m map[string]T) []string {
	ids := make([]string, 0, len(m))
	for id := range m {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}
func (s *Store) Admit() (Activation, Packet, error) {
	var a Activation
	var packet Packet
	e := s.Update("operator", "activation.started", "admit within intention allocations and limits", func(st *State) error {
		now := s.Now()
		if st.Mode != "running" {
			return fmt.Errorf("instance %s", st.Mode)
		}
		if !st.Config.FreezeAt.IsZero() && !now.Before(st.Config.FreezeAt) {
			return fmt.Errorf("freeze deadline reached")
		}
		n, live := used(*st, now)
		if n >= st.Config.StartsPerHour {
			return fmt.Errorf("hourly start limit")
		}
		if len(live) >= st.Config.Concurrency {
			return fmt.Errorf("concurrency limit")
		}
		p, reason, e := selectPursuit(st, now)
		if e != nil {
			return e
		}
		a = Activation{ID: NewID("act"), Pursuit: p.ID, Phase: "work", Started: now, Deadline: now.Add(time.Duration(st.Config.DeadlineSeconds) * time.Second), Status: "running", Reason: reason, Share: p.Share, Config: st.Config, Usage: Usage{Quality: "unavailable", Basis: "unknown"}, CodeRevision: BuildRevision()}
		if !st.Config.FreezeAt.IsZero() && st.Config.FreezeAt.Before(a.Deadline) {
			a.Deadline = st.Config.FreezeAt
		}
		for _, id := range sortedKeys(st.Activations) {
			old := st.Activations[id]
			if old.Pursuit == p.ID && old.Phase == "rectification_pending" {
				a.Phase = "rectification"
				a.RecoveryOf = id
				a.RecoveryAttempts = old.RecoveryAttempts + 1
				a.WorkSummary = old.WorkSummary
				old.Phase = "recovery_admitted"
				st.Activations[id] = old
				break
			}
		}
		i := st.Items[p.ID]
		i.Attention.EffortState = "waiting"
		i.Attention.NextAt = time.Time{}
		i.Attention.DeferredAt = now
		i.Revision++
		i.Actor = "operator"
		i.Reason = "attention request admitted"
		i.UpdatedAt = now
		st.Items[p.ID] = i
		for id, t := range st.Timers {
			if t.Active && t.Pursuit == p.ID && !t.Due.After(now) {
				if t.IntervalSeconds == 0 {
					t.Active = false
				} else {
					step := time.Duration(t.IntervalSeconds) * time.Second
					t.Due = t.Due.Add((now.Sub(t.Due)/step + 1) * step)
				}
				st.Timers[id] = t
			}
		}
		for id, w := range st.Wakes {
			if w.Pursuit == p.ID && w.ConsumedBy == "" && !w.At.After(now) {
				w.ConsumedBy = a.ID
				st.Wakes[id] = w
			}
		}
		st.Starts++
		st.Activations[a.ID] = a
		packet = Assemble(*st, a)
		if packet.Error != "" {
			return fmt.Errorf("%s", packet.Error)
		}
		b := Marshal(packet)
		a.ContextHash = Digest(b)
		st.Activations[a.ID] = a
		if e = os.MkdirAll(s.path("contexts"), 0700); e != nil {
			return e
		}
		return Atomic(s.path("contexts/"+a.ID+".json"), b)
	})
	return a, packet, e
}

type Outcome struct {
	Summary    string      `json:"summary"`
	Changes    Batch       `json:"changes"`
	Usage      Usage       `json:"usage"`
	Completion *Completion `json:"completion,omitempty"`
	Session    string      `json:"session,omitempty"`
}

func (s *Store) Finish(id string, out Outcome, runErr error) error {
	return s.Update("operator", "activation.finished", "durable activation outcome", func(st *State) error {
		a, ok := st.Activations[id]
		if !ok || a.Status != "running" {
			return fmt.Errorf("activation is not running")
		}
		if a.Completion != nil && runErr != nil {
			out.Summary = "Rectification durably committed; final harness delivery failed: " + runErr.Error()
			runErr = nil
		}
		if runErr == nil && a.Completion == nil && !s.Now().Before(a.Deadline) {
			runErr = fmt.Errorf("activation deadline expired")
		}
		if runErr == nil && a.Completion == nil {
			runErr = fmt.Errorf("rectification incomplete: phase_complete required")
		}
		if runErr == nil {
			a.Status = "completed"
			a.Phase = "completed"
		} else {
			pending(&a, s.Now())
			out.Summary = runErr.Error()
		}
		a.Finished = s.Now()
		a.Summary = out.Summary
		a.Usage = out.Usage
		if a.Usage.Quality == "" {
			a.Usage.Quality = "unavailable"
		}
		a.Process = Process{}
		st.Activations[id] = a
		return nil
	})
}
func pending(a *Activation, now time.Time) {
	a.Status = "failed"
	a.Phase = "rectification_pending"
	a.RetryAt = now.Add(time.Minute * time.Duration(min(10, 1<<min(a.RecoveryAttempts, 5))))
}
