package core

import (
	"sort"
	"time"
)

// Derived resource facts, not an attention policy or promise of admission.
type AttentionRunway struct {
	AsOf                   time.Time `json:"as_of"`
	FutureStarts           int       `json:"future_starts_before_refill"`
	Boundary               time.Time `json:"boundary"`
	BudgetAvailableAt      time.Time `json:"budget_available_at"`
	NoCapacityBeforeFreeze bool      `json:"no_capacity_before_freeze"`
	TimerFirings           int64     `json:"timer_firings_before_boundary"`
	Basis                  string    `json:"basis"`
}

func attentionRunway(st State, now time.Time) AttentionRunway {
	expiries := []time.Time{}
	for _, a := range st.Activations {
		if a.Started.After(now.Add(-time.Hour)) {
			expiries = append(expiries, a.Started.Add(time.Hour))
		}
	}
	sort.Slice(expiries, func(i, j int) bool { return expiries[i].Before(expiries[j]) })
	v := AttentionRunway{AsOf: now, FutureStarts: max(0, st.Config.StartsPerHour-len(expiries)), BudgetAvailableAt: now,
		Boundary: now.Add(time.Hour), Basis: "Projection, not a reservation or guarantee. Current activation is already charged. Timer firings are requests, may coalesce, and compete with other work; observers still need response capacity. No required utilization or polling cadence."}
	if len(expiries) > 0 {
		v.Boundary = expiries[0]
	}
	if v.FutureStarts == 0 && st.Config.StartsPerHour > 0 {
		v.BudgetAvailableAt = expiries[len(expiries)-st.Config.StartsPerHour]
	}
	if !st.Config.FreezeAt.IsZero() {
		if st.Config.FreezeAt.Before(v.Boundary) {
			v.Boundary = st.Config.FreezeAt
		}
		v.NoCapacityBeforeFreeze = !v.BudgetAvailableAt.Before(st.Config.FreezeAt)
	}
	if !v.Boundary.After(now) {
		return v
	}
	portfolio := st.Portfolio()
	for _, timer := range st.Timers {
		p, ok := portfolio[timer.Pursuit]
		if !timer.Active || !ok || p.Share <= 0 || p.Status == "stopped" {
			continue
		}
		due := timer.Due
		if due.Before(now) {
			due = now
		}
		if !due.Before(v.Boundary) {
			continue
		}
		v.TimerFirings++
		if timer.IntervalSeconds > 0 {
			v.TimerFirings += int64((v.Boundary.Sub(due) - time.Nanosecond) / (time.Duration(timer.IntervalSeconds) * time.Second))
		}
	}
	return v
}
