package core

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"syscall"
	"unicode/utf8"
)

type Tool struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	InputSchema map[string]any `json:"inputSchema"`
}

func schema(props map[string]any, required ...string) map[string]any {
	value := map[string]any{"type": "object", "properties": props, "additionalProperties": false}
	if len(required) > 0 {
		value["required"] = required
	}
	return value
}
func prop(t, description string) map[string]any {
	return map[string]any{"type": t, "description": description}
}
func completionSchema() map[string]any {
	return schema(map[string]any{
		"expected_seq": map[string]any{"type": "integer", "minimum": 1, "description": "Required current state sequence. Read state(section=config) immediately before completion; if concurrent/new evidence changes it, reconsider and retry with the new sequence. Never omit or guess."},
		"considered":   map[string]any{"type": "array", "items": prop("string", "Existing canonical node, item, edge, activation, receipt, consequence, timer, wake or program ID; or an exact source ref already retained in item.sources"), "description": "References actually considered; may be empty or omitted. Unknown paths are not canonical references; describe other readings in the summary if useful. Acknowledgment is not proof of understanding."},
		"outstanding":  map[string]any{"type": "array", "items": prop("string", "Existing durable consequence ID"), "description": "Material implications remaining unresolved; may be empty or omitted. Not free text or source paths."},
		"continuation": map[string]any{"type": "string", "enum": []string{"continue", "wait", "dormant", "stop"}, "description": "continue requests next useful work as soon as eligible under budgets, not waiting for an observer. wait defers until next_at or default reconsideration; the configured whole-self intention retains its protected reconsideration clock even with a later next_at. An undated wait's delay starts at accepted completion, not the earlier packet time. The response returns committed attention timing. dormant awaits an event/timer or explicit resumption without automatic reconsideration; stop disables ordinary admission. Existing observers remain independent of this choice. No required utilization or preferred choice."},
		"next_at":      map[string]any{"type": "string", "format": "date-time", "description": "Optional future RFC3339 attention time before terminal freeze, if configured. For the configured whole-self intention, an earlier return is honored but a later time cannot defer protected reconsideration beyond reconsider_seconds. Omit for dormant/stop. No need to invent a time or ongoing duty."},
		"coverage":     map[string]any{"type": "string", "minLength": 1, "description": "Actual continuation/observation coverage and limits; explicitly uncovered rest is valid. A timer or observer does not reserve response capacity."},
		"reason":       map[string]any{"type": "string", "minLength": 1, "description": "Why this continuation is warranted; no knowledge edit or successor work is mandatory."},
	}, "expected_seq", "continuation", "coverage", "reason")
}
func Tools() []Tool {
	page := map[string]any{"offset": prop("integer", "Zero-based offset"), "limit": prop("integer", "Maximum records, 1 to 100; further byte bounds apply")}
	return []Tool{
		{"item", "Read one canonical item with stable ID, revision, sources and optional attention.", schema(map[string]any{"id": prop("string", "Item ID")}, "id")},
		{"context", "Refresh applicable global/domain context before changing activity; arbitrary shell activity is not automatically classified.", schema(map[string]any{"query": prop("string", "Current activity/domain/artifact description")})},
		{"phase_complete", "Complete rectification with current expected_seq, considered references, pending consequences and explicit continuation. No knowledge edit or separate contract read is required when these field definitions suffice.", schema(map[string]any{"completion": completionSchema()}, "completion")},
		{"acknowledge_observations", "Explicitly clear named observation wake requests already accounted for, including arrivals during work. Requires current sequence and reason; preserves evidence and existing consumers. Does not resolve obligations, stop observers, suppress new events or complete rectification. Optional in work or rectification; consideration alone does not clear a wake.", schema(map[string]any{"expected_seq": prop("integer", "Required current state sequence"), "ids": map[string]any{"type": "array", "items": prop("string", "Exact existing observation/wake ID"), "minItems": 1, "uniqueItems": true}, "reason": prop("string", "Why these observations need no extra immediate activation; unresolved duties may remain")}, "expected_seq", "ids", "reason")},
		{"resume_work", "If operator-enabled, request one return to work after this rectification turn ends, within the same activation's original work deadline. This turn remains state-only. End with a summary without phase_complete; phase_complete instead cancels the request. No extra start or authority; final rectification still required.", schema(map[string]any{"expected_seq": prop("integer", "Current state sequence"), "reason": prop("string", "Why further work is useful now; at most 1000 bytes")}, "expected_seq", "reason")},
		{"record", "Read a complete canonical record as bounded JSON text slices, including oversized metadata.", schema(map[string]any{"section": prop("string", "nodes, items, edges, consequences, timers, wakes, activations, programs, receipts"), "id": prop("string", "Record ID"), "offset": page["offset"], "limit": prop("integer", "Text bytes")}, "section", "id")},
		{"event", "Read one complete immutable event as bounded JSON text slices, including historical item revisions.", schema(map[string]any{"sequence": prop("integer", "Event sequence"), "offset": page["offset"], "limit": prop("integer", "Text bytes")}, "sequence")},
		{"program_stop", "Disable and stop a supervised foreground program; retains its registration for inspection and revision.", schema(map[string]any{"id": prop("string", "Program ID")}, "id")},
		{"watch", "Observe a file or HTTP GET source for changes using an existing supervised program, without cognitive polling or writing a script. Returns the initial contents for you to handle. Future changes/errors request attention; response still needs budget. Allowed during rectification as read-only observation and program registration. Stop with program_stop.", schema(map[string]any{"id": prop("string", "Program ID"), "intention": prop("string", "Optional target intention, defaults to current"), "reason": prop("string", "Why observe this source"), "path": prop("string", "Regular file path, exclusive with url"), "url": prop("string", "HTTP(S) GET endpoint with stable response, exclusive with path"), "headers": map[string]any{"type": "object", "additionalProperties": map[string]any{"type": "string"}}, "interval_seconds": prop("integer", "Poll interval, 5..3600; default 15")}, "id", "reason")},
		{"state", "Read canonical configuration or paginated nodes, items, consequences, timers, wakes, activations, programs, receipts. Includes sequence for optimistic attention edits.", schema(map[string]any{"section": prop("string", "config, nodes, items, edges, consequences, timers, wakes, activations, programs, receipts"), "offset": page["offset"], "limit": page["limit"]}, "section")},
		{"node", "Read one complete untyped node with its structured items (<5000 bytes). Links/history are separate.", schema(map[string]any{"id": prop("string", "Node ID"), "offset": page["offset"], "limit": prop("integer", "Reserved pagination fields; node contents fit the node bound")}, "id")},
		{"search", "Search beyond the local graph neighborhood. Reports semantic availability and stale entries.", schema(map[string]any{"query": prop("string", "What understanding/evidence are you looking for?"), "offset": page["offset"], "limit": page["limit"]}, "query")},
		{"links", "Paginate incoming and outgoing semantic links; no structural degree cap.", schema(map[string]any{"id": prop("string", "Node or item ID"), "offset": page["offset"], "limit": page["limit"]}, "id")},
		{"history", "Read immutable event history for an ID, paginated. next_offset=-1 ends the scan; total=-1 means the full archive has not been counted. Large events return their sequence for event byte pagination.", schema(map[string]any{"id": prop("string", "Record ID, or empty for all history"), "offset": page["offset"], "limit": page["limit"]})},
		{"mutate", "Atomic node/item/link/consequence/timer/program writeback. Nodes/items require expected_revision (0=new); attention edits and observation acknowledgments require expected_seq. Consult contract for missing field definitions. No knowledge edit is mandatory.", schema(map[string]any{"reason": prop("string", "Why this durable change is warranted"), "changes": map[string]any{"type": "object", "description": "Batch: nodes:[{expected_revision,node}], items:[{expected_revision,item}], edges, remove_edges, expected_seq, consequences, acknowledge:[{id,intention,reason}], acknowledge_observations:[wake_id], timers, programs"}}, "reason", "changes")},
		{"contract", "Read a paginated first-class tool/writeback contract with complete field definitions and examples.", schema(map[string]any{"offset": page["offset"], "limit": prop("integer", "Text bytes")})},
		{"reindex", "Rebuild the optional local semantic index; does not mutate canonical understanding.", schema(map[string]any{})},
		{"artifact", "Read/write UTF-8 text files below artifacts/. Results distinguish artifact-tool path from workspace_path (including artifacts/). Written means local file only, not delivery. Reads are byte-paginated at character boundaries; follow next_offset. Use workspace tools for binary files. Writing requires workspace permission. Runtime files are not an artifact interface.", schema(map[string]any{"path": prop("string", "Relative to artifacts/: use result.json, not artifacts/result.json"), "write": prop("string", "Optional complete replacement content"), "offset": page["offset"], "limit": prop("integer", "Maximum source bytes per read; use at least 4 for arbitrary UTF-8 text")}, "path")},
		{"effect", "Reserve or complete a receipt for an explicitly performed effect. Reserve BEFORE acting. Existing uncertain/reserved receipts are not permission to repeat an effect.", schema(map[string]any{"key": prop("string", "Stable idempotency key"), "fingerprint": prop("string", "Stable request digest"), "status": prop("string", "reserved, completed, uncertain, failed"), "detail": prop("string", "Evidence/provider receipt, without credentials")}, "key", "fingerprint", "status")},
		{"http_effect", "Perform one receipt-backed HTTP write with a stable idempotency key. Credential headers may refer to an instance-owned environment variable. Uncertain effects require reconciliation, not automatic retry.", schema(map[string]any{"key": prop("string", "Idempotency key"), "url": prop("string", "HTTP(S) destination"), "method": prop("string", "POST, PUT, PATCH or DELETE"), "body": prop("string", "Request body"), "headers": map[string]any{"type": "object", "additionalProperties": map[string]any{"type": "string"}}}, "key", "url", "method")},
	}
}

type pageResult struct {
	Sequence int64 `json:"sequence"`
	Items    []any `json:"items"`
	Total    int   `json:"total"`
	Next     int   `json:"next_offset"`
}

func pageItems(seq int64, items []any, offset, limit, budget int) pageResult {
	r := pageResult{Sequence: seq, Items: []any{}, Total: len(items), Next: -1}
	offset = min(max(0, offset), len(items))
	for i := offset; i < len(items) && len(r.Items) < limit; i++ {
		r.Items = append(r.Items, items[i])
		b, _ := json.Marshal(r)
		if len(b) > budget-128 {
			r.Items = r.Items[:len(r.Items)-1]
			if len(r.Items) == 0 {
				r.Items = append(r.Items, map[string]any{"oversized": true, "offset": i, "hint": "use record/event byte pagination"})
			}
			break
		}
	}
	if offset+len(r.Items) < len(items) {
		r.Next = offset + len(r.Items)
	}
	return r
}
func (s *Store) Call(ctx context.Context, actor, name string, raw json.RawMessage) (result any, callErr error) {
	st, e := s.Read()
	if e != nil {
		return nil, e
	}
	defer func() {
		if callErr == nil && len(Marshal(result)) > st.Config.ResponseBytes {
			result = nil
			callErr = fmt.Errorf("response exceeds configured bound; use fewer items or record/event byte pagination")
		}
	}()
	var q struct {
		Sequence        int64             `json:"sequence"`
		ExpectedSeq     *int64            `json:"expected_seq"`
		Section         string            `json:"section"`
		ID              string            `json:"id"`
		IDs             []string          `json:"ids"`
		Query           string            `json:"query"`
		Offset          int               `json:"offset"`
		Limit           int               `json:"limit"`
		Reason          string            `json:"reason"`
		Changes         Batch             `json:"changes"`
		Path            string            `json:"path"`
		Write           *string           `json:"write"`
		Key             string            `json:"key"`
		Fingerprint     string            `json:"fingerprint"`
		Status          string            `json:"status"`
		Detail          string            `json:"detail"`
		URL             string            `json:"url"`
		Method          string            `json:"method"`
		Body            string            `json:"body"`
		Headers         map[string]string `json:"headers"`
		Completion      Completion        `json:"completion"`
		Intention       string            `json:"intention"`
		IntervalSeconds int               `json:"interval_seconds"`
	}
	if e = Decode(raw, &q); e != nil {
		return nil, e
	}
	if name == "phase_complete" {
		// A zero-valued Go integer cannot distinguish an omitted JSON field from
		// a supplied stale sequence. Preserve the durable Completion schema and
		// optimistic-concurrency check; diagnose missing input at the tool edge.
		var fields struct {
			Completion *struct {
				ExpectedSeq *int64 `json:"expected_seq"`
			} `json:"completion"`
		}
		if e = json.Unmarshal(raw, &fields); e != nil {
			return nil, e
		}
		if fields.Completion == nil || fields.Completion.ExpectedSeq == nil {
			return nil, fmt.Errorf("completion.expected_seq is required: read state(section=config), consider any concurrent/new evidence, and supply its current sequence; no completion was committed")
		}
	}
	if q.Offset < 0 {
		return nil, fmt.Errorf("offset must be nonnegative")
	}
	limit := q.Limit
	if limit == 0 {
		limit = 20
	}
	limit = min(max(1, limit), 100)
	budget := st.Config.ResponseBytes
	if actor != "operator" {
		a := st.Activations[actor]
		if a.Phase == "rectification" && ((name == "artifact" && q.Write != nil) || name == "http_effect") {
			return nil, fmt.Errorf("rectification is state-only; preserve further implementation/effects for a work activation")
		}
		activity := ""
		if name == "artifact" && q.Write != nil {
			activity = "engineering " + q.Path
		}
		if name == "http_effect" {
			activity = "external communication " + q.URL
		}
		stamp := contextStamp(st, a, activity)
		prepared := stamp == a.PreparedContext
		for _, prior := range a.PreparedContexts {
			prepared = prepared || prior == stamp
		}
		if activity != "" && !prepared {
			p, e := s.refreshContext(ctx, actor, activity, true)
			if e == nil {
				e = s.Update(actor, "context.prepared", "applicable context delivered before mediated action", func(st *State) error {
					x := st.Activations[actor]
					x.PreparedContext = stamp
					x.PreparedContexts = append(x.PreparedContexts, stamp)
					if len(x.PreparedContexts) > 8 {
						x.PreparedContexts = x.PreparedContexts[len(x.PreparedContexts)-8:]
					}
					st.Activations[actor] = x
					return nil
				})
			}
			return map[string]any{"deferred": true, "instruction": "Read newly applicable context, then retry this tool; no requested effect has occurred", "context": p}, e
		}
	}
	switch name {
	case "acknowledge_observations":
		if len(q.IDs) == 0 {
			return nil, fmt.Errorf("nonempty exact observation IDs required")
		}
		e = s.Mutate(actor, q.Reason, Batch{ExpectedSeq: q.ExpectedSeq, AcknowledgeObservations: q.IDs})
		return map[string]any{"acknowledged": e == nil}, e
	case "watch":
		return s.RegisterWatch(ctx, actor, q.ID, q.Intention, q.Reason, WatchSpec{Path: q.Path, URL: q.URL, Headers: q.Headers, IntervalSeconds: q.IntervalSeconds})
	case "context":
		return s.refreshContext(ctx, actor, q.Query, true)
	case "item":
		i, ok := st.Items[q.ID]
		if !ok {
			for _, section := range []string{"nodes", "edges", "consequences", "timers", "wakes", "activations", "programs", "receipts"} {
				v := reflect.ValueOf(st)
				for n := 0; n < v.NumField(); n++ {
					if v.Type().Field(n).Tag.Get("json") == section && v.Field(n).MapIndex(reflect.ValueOf(q.ID)).IsValid() {
						return nil, fmt.Errorf("item not found: %q exists in %s; use record(section=%q, id=%q) for its canonical contents", q.ID, section, section, q.ID)
					}
				}
			}
			return nil, fmt.Errorf("item not found")
		}
		return i, nil
	case "phase_complete":
		attention, err := s.completePhase(actor, q.Completion)
		if err != nil {
			return map[string]any{"completed": false}, err
		}
		return map[string]any{"completed": true, "attention": attention}, nil
	case "resume_work":
		if actor == "operator" {
			return nil, fmt.Errorf("work return belongs to a live activation")
		}
		if q.ExpectedSeq == nil {
			return nil, fmt.Errorf("expected_seq is required; read current state before requesting work return")
		}
		if err := s.RequestWorkReturn(actor, *q.ExpectedSeq, q.Reason); err != nil {
			return nil, err
		}
		return map[string]any{"requested": true, "work_deadline": workBoundary(st.Activations[actor]), "instruction": "This turn remains state-only. End with a summary without phase_complete to return to work; phase_complete cancels this request. Final rectification remains required."}, nil
	case "program_stop":
		if e = s.CheckActor(actor); e != nil {
			return nil, e
		}
		e = s.StopProgramAs(actor, q.ID)
		return map[string]any{"stopped": e == nil}, e
	case "record":
		v := reflect.ValueOf(st)
		typ := v.Type()
		for i := 0; i < v.NumField(); i++ {
			if typ.Field(i).Tag.Get("json") == q.Section && v.Field(i).Kind() == reflect.Map {
				x := v.Field(i).MapIndex(reflect.ValueOf(q.ID))
				if !x.IsValid() {
					return nil, fmt.Errorf("record not found")
				}
				return textSlice(Marshal(x.Interface()), q.Offset, q.Limit, budget)
			}
		}
		return nil, fmt.Errorf("unknown section")
	case "event":
		if q.Sequence < 1 {
			return nil, fmt.Errorf("positive event sequence required")
		}
		events, e := s.History("", int(q.Sequence-1), 1)
		if e != nil {
			return nil, e
		}
		if len(events) != 1 {
			return nil, fmt.Errorf("event not found")
		}
		return textSlice(Marshal(events[0]), q.Offset, q.Limit, budget)
	case "state":
		if q.Section == "config" {
			return map[string]any{"sequence": st.Seq, "mode": st.Mode, "config": st.Config}, nil
		}
		v := reflect.ValueOf(st)
		typ := v.Type()
		items := []any{}
		found := false
		for i := 0; i < v.NumField(); i++ {
			if typ.Field(i).Tag.Get("json") != q.Section || v.Field(i).Kind() != reflect.Map {
				continue
			}
			found = true
			m := v.Field(i)
			keys := m.MapKeys()
			sort.Slice(keys, func(i, j int) bool { return keys[i].String() < keys[j].String() })
			for _, k := range keys {
				x := m.MapIndex(k).Interface()
				items = append(items, x)
			}
		}
		if !found {
			return nil, fmt.Errorf("unknown section")
		}
		return pageItems(st.Seq, items, q.Offset, limit, budget), nil
	case "node":
		_, ok := st.Nodes[q.ID]
		if !ok {
			return nil, fmt.Errorf("node not found")
		}
		return NodeContext(st, q.ID), nil
	case "search":
		r, e := s.Search(ctx, q.Query, q.Offset, limit)
		if e != nil {
			return nil, e
		}
		items := []any{}
		for _, h := range r.Hits {
			items = append(items, h)
		}
		p := pageItems(st.Seq, items, 0, limit, budget-256)
		r.Hits = r.Hits[:len(p.Items)]
		if len(r.Hits)+q.Offset < r.Total {
			r.Next = q.Offset + len(r.Hits)
		}
		return r, nil
	case "links":
		items := []any{}
		ids := []string{}
		for id, e := range st.Edges {
			if e.From == q.ID || e.To == q.ID {
				ids = append(ids, id)
			}
		}
		sort.Strings(ids)
		for _, id := range ids {
			items = append(items, st.Edges[id])
		}
		return pageItems(st.Seq, items, q.Offset, limit, budget), nil
	case "history":
		// One-record lookahead distinguishes a full terminal page from a
		// prefix, without enumerating the archive just to report a total.
		events, e := s.History(q.ID, q.Offset, limit+1)
		if e != nil {
			return nil, e
		}
		items := []any{}
		for _, ev := range events {
			b, _ := json.Marshal(ev)
			if len(b) > budget/2 {
				ref := map[string]any{"sequence": ev.Seq, "at": ev.At, "actor": ev.Actor, "reason": ev.Reason, "changes": len(ev.Changes), "oversized": true}
				if len(Marshal(ref)) > budget/2 {
					// Metadata can itself be large. Keep the immutable address,
					// not a generic oversized placeholder without a retrieval key.
					ref = map[string]any{"sequence": ev.Seq, "oversized": true}
				}
				items = append(items, ref)
			} else {
				items = append(items, ev)
			}
		}
		p := pageItems(st.Seq, items, 0, limit, budget)
		p.Total = -1
		if p.Next >= 0 {
			p.Next = q.Offset + len(p.Items)
		}
		return p, nil
	case "mutate":
		if !st.Config.Workspace && len(q.Changes.Programs) > 0 {
			return nil, fmt.Errorf("programs require explicit workspace permission")
		}
		e = s.Mutate(actor, q.Reason, q.Changes)
		return map[string]any{"accepted": e == nil}, e
	case "contract":
		offset := min(q.Offset, len(Contract))
		end := min(len(Contract), offset+budget/2)
		if q.Limit > 0 {
			end = min(end, offset+q.Limit)
		}
		next := -1
		if end < len(Contract) {
			next = end
		}
		return map[string]any{"text": Contract[offset:end], "next_offset": next}, nil
	case "reindex":
		if e = s.CheckActor(actor); e != nil {
			return nil, e
		}
		e = s.Reindex(ctx)
		return map[string]any{"rebuilt": e == nil}, e
	case "artifact":
		path, e := s.ArtifactPath(q.Path)
		if e != nil {
			return nil, e
		}
		references := map[string]any{"path": q.Path, "workspace_path": filepath.ToSlash(filepath.Join("artifacts", filepath.Clean(q.Path)))}
		if q.Write != nil {
			if !st.Config.Workspace {
				return nil, fmt.Errorf("workspace writes not enabled")
			}
			if e = s.CheckActor(actor); e != nil {
				return nil, e
			}
			if len(*q.Write) > 1<<20 {
				return nil, fmt.Errorf("artifact write too large; use workspace tools")
			}
			references["written"] = true
			if len(Marshal(references)) > budget {
				return nil, fmt.Errorf("response budget cannot fit artifact reference metadata; use a shorter relative path or workspace tools")
			}
			if e = os.MkdirAll(filepath.Dir(path), 0700); e != nil {
				return nil, e
			}
			e = Atomic(path, []byte(*q.Write))
			references["written"] = e == nil
			return references, e
		}
		// Do not block opening a FIFO disguised as an ordinary artifact.
		f, e := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
		if e != nil {
			return nil, e
		}
		defer f.Close()
		info, e := f.Stat()
		if e != nil {
			return nil, e
		}
		if !info.Mode().IsRegular() {
			return nil, fmt.Errorf("artifact reads require a regular UTF-8 text file")
		}
		if int64(q.Offset) > info.Size() {
			q.Offset = int(info.Size())
		}
		limit := textPageLimit(q.Limit, budget)
		// Bounded lookahead distinguishes a split rune from invalid source bytes.
		buf := make([]byte, limit+utf8.UTFMax)
		n, e := f.ReadAt(buf, int64(q.Offset))
		if e != nil && e != io.EOF {
			return nil, e
		}
		return textPage(buf[:n], q.Offset, limit, budget, "content", int64(q.Offset)+int64(n) < info.Size(), references)
	case "effect":
		return s.RecordEffect(actor, q.Key, q.Fingerprint, q.Status, q.Detail)
	case "http_effect":
		return s.HTTPEffect(ctx, actor, q.Key, q.Method, q.URL, q.Body, q.Headers)
	default:
		return nil, fmt.Errorf("unknown tool %s; inspect tools/list", name)
	}
}
func textPageLimit(limit, budget int) int {
	if limit <= 0 {
		return budget / 4
	}
	return min(limit, budget/4)
}

func textSlice(b []byte, offset, limit, budget int) (any, error) {
	offset = min(offset, len(b))
	return textPage(b[offset:], offset, textPageLimit(limit, budget), budget, "text", false,
		map[string]any{"bytes": len(b), "sha256": Digest(b)})
}

// Byte offsets remain exact across JSON transport. Never return a partial rune
// that encoding/json would silently replace, or skip bytes to make a page fit.
func textPage(b []byte, offset, limit, budget int, key string, more bool, fields map[string]any) (any, error) {
	if len(b) == 0 && more {
		return nil, fmt.Errorf("source changed while reading; restart pagination from current evidence")
	}
	if len(b) > 0 && !utf8.RuneStart(b[0]) {
		return nil, fmt.Errorf("offset is not a UTF-8 character boundary; use the previous page's next_offset")
	}
	end := min(len(b), limit)
	for {
		for end > 0 && end < len(b) && !utf8.RuneStart(b[end]) {
			end--
		}
		if end == 0 && len(b) > 0 {
			return nil, fmt.Errorf("page limit cannot fit the next UTF-8 character; retry with limit >= 4")
		}
		if !utf8.Valid(b[:end]) {
			return nil, fmt.Errorf("source is not valid UTF-8 text at this offset; use workspace tools for binary bytes")
		}
		next := -1
		if end < len(b) || more {
			next = offset + end
		}
		fields[key] = string(b[:end])
		fields["next_offset"] = next
		if len(Marshal(fields)) <= budget {
			return fields, nil
		}
		if end == 0 {
			return nil, fmt.Errorf("response budget cannot fit page metadata")
		}
		// JSON escaping can expand source bytes sixfold. Reduce this page,
		// keeping its precise continuation rather than rejecting all reads.
		end /= 2
	}
}

func (s *Store) CheckActor(actor string) error {
	st, e := s.Read()
	if e != nil {
		return e
	}
	if st.Mode == "frozen" || (!st.Config.FreezeAt.IsZero() && !s.Now().Before(st.Config.FreezeAt)) {
		return fmt.Errorf("instance frozen/deadline reached")
	}
	if actor == "operator" {
		return nil
	}
	a := st.Activations[actor]
	if a.Status != "running" || a.Completion != nil || !s.Now().Before(a.Deadline) {
		return fmt.Errorf("activation lease is not live")
	}
	return nil
}
func (s *Store) ArtifactPath(name string) (string, error) {
	if name == "" || filepath.IsAbs(name) {
		return "", fmt.Errorf("relative artifact path required")
	}
	clean := filepath.Clean(name)
	if clean == ".." || strings.HasPrefix(clean, ".."+string(os.PathSeparator)) {
		return "", fmt.Errorf("path escapes artifacts")
	}
	root := filepath.Join(s.Dir, "artifacts")
	path := filepath.Join(root, clean)
	// Reject symlink components, including the artifact root; no ambient file access.
	for p := path; p != s.Dir; p = filepath.Dir(p) {
		info, e := os.Lstat(p)
		if e == nil && info.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("artifact symlinks are not allowed")
		}
		if e != nil && !os.IsNotExist(e) {
			return "", e
		}
	}
	return path, nil
}

const Contract = `Concorde3 tool/writeback contract v2
All graph edits use mutate with reason and changes. Never edit runtime files or brain.json. All reads are bounded; use next_offset. Read state/config for current sequence, permissions and budgets.

Node: {id,title,status}. Untyped coherent container. Node with its current items/metadata must render <5000 UTF-8 JSON bytes, excluding links/history. Long evidence goes in referenced artifacts.
Item: {id,node,kind,text,status,approach?,sources?:[{ref,observed_at?}],applies_to?:[activity_term_or_intention_id],attention?:{weight,effort_state:"ready"|"waiting"|"dormant"|"stopped",next_at?}}. Suggested open kinds: observation, belief, intention, norm, capability, question. Status is desired/evidence state (active, attained, abandoned, disputed, retired), separate from effort_state. Attained/abandoned intentions receive no ordinary starts until reopened. Only intention items carry attention. No separate pursuit record. Stable item IDs survive moves between nodes. Replace the whole item with its current fields preserved. Runtime supplies revision, actor, reason, updated_at and attention.deferred_at. Text/weight edits do not renew the deferral clock. observed_at is when observed, never refreshed by rewriting. Retire rather than erase evidence.
Edge: {id,from,to,relation,reason}; endpoints are existing node or item IDs. Editing/removing existing links requires current expected_seq. No degree cap, allocation or authority semantics.
Batch: {expected_seq?,nodes?:[{expected_revision,node}],items?:[{expected_revision,item}],edges?,remove_edges?:[id],consequences?,acknowledge?,acknowledge_observations?:[wake_id],timers?,programs?}. expected_revision=0 creates; updates require current revision. Any attention edit or observation acknowledgment additionally requires current expected_seq. Item moves use the same ID/revision with changed node. Budget weights sum <=1; configured reconsideration intention retains broad_share of funded weight. A parked weight is not evidence of actual broader reconsideration. All can wait or stop. Admission consumes readiness once; further starts require continuation, timer or wake.
Observation acknowledgment: acknowledge_observations {expected_seq,ids:[wake_id],reason}, or the same IDs in mutate.changes.acknowledge_observations. Explicitly clear only those existing non-future wake requests already accounted for; consumed_by records the acknowledging actor if previously unconsumed. Evidence/time and prior consumers remain unchanged. Pending observations can cause another start even after wait/dormant, including arrivals during work already seen in rectification. Mere considered references do not clear them. Acknowledgment does not resolve the underlying duty, cancel timers/programs, suppress new evidence, or complete rectification. Preserve unresolved implications and choose an honest continuation; no acknowledgment is mandatory.
Consequence: {id,summary,references:[item_or_source],targets:[intention_item_id]}. Targets are existing non-retired intention items; attention need not be allocated. Immutable new evidence; creation neither allocates attention nor wakes. acknowledge:[{id,intention:intention_item_id,reason}] records consideration, not understanding or success. Preserve unresolved material implications here. Keep acknowledged-but-unresolved IDs in phase_complete.outstanding. The next packet projects the previous completion and its outstanding consequences; a later completion can omit resolved/dropped matters. previous_continuation is a revisable prior decision, not authority. recent_trajectory contains bounded reported outcomes, timestamps, usage and receipt references, not an independent verdict. Read full sources with record(section=activations,id=...) and record(section=receipts,id=...). Truncated/omitted views do not delete history.
Timer: {id,intention:intention_item_id,due:RFC3339,interval_seconds:0,active:true,reason}. 0=one-shot; recurring timers coalesce missed ticks. Disable/rebind before stopping its intention.
New active timer requests and next_at requests must precede the configured terminal freeze, when one exists. A wake after that boundary cannot execute. Keep longer-range desires in memory; choose feasible attention or honestly leave coverage absent. This does not guarantee capacity for a wake inside the horizon.
Waiting without next_at becomes eligible again after config.deferral_seconds (0/omitted = 600), or config.reconsider_seconds for the whole-self reconsideration intention (0/omitted = 1800). Portfolio reconsider_at exposes current-state eligibility. Completing an undated wait restarts that delay at accepted completion, so a pre-completion projection is not the resulting return time. phase_complete returns the attention view committed by that transaction; later attention changes may supersede it. Explicit next_at replaces the default for local intentions. For the configured whole-self intention, it may bring reconsideration earlier but cannot defer that protected opportunity beyond reconsider_seconds; the requested later next_at remains recorded as intent, not guaranteed silence. These are budget-limited opportunities, not observation coverage or delivery guarantees. Dormant opts out of automatic reconsideration but permits explicit timers/events; stopped opts out of both. Dormant/stopped cannot carry next_at. Pending rectification recovery remains separate from ordinary work readiness. No need to activate inert knowledge or manufacture work.
Program: {id,command:[executable,args...],intention:intention_item_id,enabled:true,interval_seconds:0}. Supervised foreground program; never daemonize or escape process group. 0=long-running service, exits retry with bounded delay. Disable running programs with program_stop before editing. Workspace permission required. Environment CONCORDE3_INSTANCE and CONCORDE3_PURSUIT allow: concorde3 notify INSTANCE INTENTION_ID UNIQUE_KEY EVIDENCE. Duplicates coalesce; changed evidence needs a new key. Exit failure wakes; stdout alone does not. Build observers against normal external interfaces; external customers never need private scheduler addresses.
Program status succeeded means exit zero, not verified function. last_stderr is a bounded diagnostic excerpt. First stderr on a zero exit creates one ordinary observation per program command/intention; it may be benign. Repeated stderr does not keep waking. Inspect .concorde2/program-logs/ID.log and validate the real trigger/output path before claiming coverage.
watch {id,reason,intention?,path OR url,headers?,interval_seconds?} is an optional script-free observation capability, backed by a supervised program. It reads a regular file or HTTP GET endpoint (no redirects, max 1 MiB) now and returns its initial contents; these may already need handling. Subsequent byte changes and read errors atomically create ordinary wakes; quiet observations use no activations. Use a stable endpoint; fluctuating timestamps also count as changes. Default interval 15s, allowed 5..3600. Workspace permission required. Inspect programs for observed fingerprint/error; stop with program_stop. Registration is permitted in rectification. No reservation or delivery guarantee; remaining attention must still cover response. Custom programs remain available for other observation needs.

Work turn: use tools and workspace within authority, commit useful state as you work, then return {"summary":"actual outcome"}. A distinct rectification message follows in this activation.
Rectification turn: update graph, connections, attention and timers/program registrations; do not implement products or perform new external effects. Read the editable rectification practice. End with phase_complete {completion:{expected_seq:CURRENT_SEQUENCE,considered:[canonical_id],outstanding:[consequence_id],continuation:continue|wait|dormant|stop,next_at?:FUTURE_TIMESTAMP,coverage:DESCRIPTION,reason:REASON}}. continue requests next useful work as soon as eligible under budgets, not waiting for an observer; wait defers until next_at or the default reconsideration time; dormant requires an event/timer or explicit resumption; stop disables ordinary admission (cancel active timers first). Existing observers remain independent of this choice. No graph edits, search quota, or extra goals required. Considered IDs may reference existing nodes, items, edges, activations, receipts, consequences, wakes or programs; unresolved implications in outstanding must be durable consequence IDs. Both lists may be empty or omitted. Read fresh sequence immediately before completion; expected_seq is required, never inferred. Reconcile conflicts. Then return a concise summary. Interrupted rectification is recovered under normal budgets; never blindly repeat effects.

Global/work/rectification bindings are required operator-configured item references. applies_to suggests relevance to intention IDs or activity terms (case-insensitive), not compulsory full inclusion. Applicable norms precede other applicable memories, but candidates may be omitted to fit the packet; omitted items remain available through item/search/state/links. Context refresh supplies relevant distant items automatically; semantic access is optional, explicit and revision-checked. Before new domain work call context with its activity description. Mediated artifact writes/HTTP effects may return deferred=true and new context BEFORE acting; read it and retry. Arbitrary shell commands cannot be perfectly classified.

Example mutate: {"reason":"New evidence undermines assumption","changes":{"items":[{"expected_revision":0,"item":{"id":"finding","node":"undertaking","kind":"observation","text":"The expected outcome was absent in the inspected result; cause unknown.","status":"active","sources":[{"ref":"artifacts/result.json"}]}}],"edges":[{"id":"finding-concerns-purpose","from":"finding","to":"purpose","relation":"concerns","reason":"Evidence about desired outcome"}]}}

Considered evidence can also cite existing timer IDs and exact source refs already retained in item.sources. This records consideration only: it never reads a file, refreshes its observation time, makes a source a graph endpoint or requests work. Unknown paths remain invalid; describe other inspections in the summary without creating memory just for the form.

Artifacts are ordinary company files, not canonical memory. effect reserves receipts BEFORE acting; an existing reserved/uncertain receipt is not permission to repeat. Provider idempotency/actual response determines recoverability. Credentials may be used within authority, never copied into public claims or Git.
`
