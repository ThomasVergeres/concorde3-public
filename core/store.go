package core

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"syscall"
	"time"
)

type Change struct {
	Field  string          `json:"field"`
	Key    string          `json:"key,omitempty"`
	Value  json.RawMessage `json:"value,omitempty"`
	Delete bool            `json:"delete,omitempty"`
}
type Event struct {
	Seq     int64     `json:"seq"`
	At      time.Time `json:"at"`
	Actor   string    `json:"actor"`
	Reason  string    `json:"reason"`
	Kind    string    `json:"kind"`
	Changes []Change  `json:"changes"`
}
type envelope struct {
	Event json.RawMessage `json:"event"`
	Hash  string          `json:"hash"`
}
type Store struct {
	Dir string
	Now func() time.Time
}

func Open(dir string) *Store {
	p, _ := filepath.Abs(dir)
	return &Store{Dir: p, Now: func() time.Time { return time.Now().UTC() }}
}
func (s *Store) path(name string) string { return filepath.Join(s.Dir, Runtime, name) }
func Digest(b []byte) string             { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func Atomic(path string, b []byte) error {
	f, e := os.CreateTemp(filepath.Dir(path), ".replace-")
	if e != nil {
		return e
	}
	name := f.Name()
	defer os.Remove(name)
	if e = f.Chmod(0600); e == nil {
		_, e = f.Write(b)
	}
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return e
	}
	if e = os.Rename(name, path); e != nil {
		return e
	}
	d, e := os.Open(filepath.Dir(path))
	if e != nil {
		return e
	}
	defer d.Close()
	return d.Sync()
}
func (s *Store) lock() (*os.File, error) {
	if e := os.MkdirAll(s.path(""), 0700); e != nil {
		return nil, e
	}
	f, e := os.OpenFile(s.path("lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX); e != nil {
		f.Close()
		return nil, e
	}
	return f, nil
}
func unlock(f *os.File) { _ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN); _ = f.Close() }
func (s *Store) Init(goal string) error {
	l, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock(l)
	if _, e = os.Stat(s.path("events.jsonl")); !errors.Is(e, os.ErrNotExist) {
		return fmt.Errorf("instance already exists or cannot be inspected")
	}
	state := NewState(goal)
	if e = Validate(state); e != nil {
		return e
	}
	return s.commit(State{}, state, "operator", "initialize", "initial purpose")
}
func (s *Store) Read() (State, error) {
	l, e := s.lock()
	if e != nil {
		return State{}, e
	}
	defer unlock(l)
	return s.load()
}
func (s *Store) Update(actor, kind, reason string, fn func(*State) error) error {
	if actor == "" || reason == "" {
		return fmt.Errorf("actor and reason required")
	}
	l, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock(l)
	before, e := s.load()
	if e != nil {
		return e
	}
	if actor != "operator" {
		if before.Mode == "frozen" || (!before.Config.FreezeAt.IsZero() && !s.Now().Before(before.Config.FreezeAt)) {
			return fmt.Errorf("instance frozen or deadline reached")
		}
		a, ok := before.Activations[actor]
		if !ok || a.Status != "running" || !s.Now().Before(a.Deadline) {
			return fmt.Errorf("activation lease is not live")
		}
		if a.Completion != nil {
			return fmt.Errorf("rectification already completed; no further state mutations in this activation")
		}
	}
	after := clone(before)
	if e = fn(&after); e != nil {
		return e
	}
	if e = Validate(after); e != nil {
		return e
	}
	// Idempotent observations/receipts must not manufacture new evidence revisions
	// and starve rectification with conflicts when an observer repeats an event.
	if reflect.DeepEqual(before, after) {
		return nil
	}
	return s.commit(before, after, actor, kind, reason)
}
func diff(a, b State) []Change {
	var out []Change
	av, bv := reflect.ValueOf(a), reflect.ValueOf(b)
	typ := av.Type()
	for i := 0; i < av.NumField(); i++ {
		field := typ.Field(i).Tag.Get("json")
		if field == "seq" {
			continue
		}
		x, y := av.Field(i), bv.Field(i)
		if x.Kind() != reflect.Map {
			if !reflect.DeepEqual(x.Interface(), y.Interface()) {
				v, _ := json.Marshal(y.Interface())
				out = append(out, Change{Field: field, Value: v})
			}
			continue
		}
		for _, k := range y.MapKeys() {
			v := y.MapIndex(k)
			old := x.MapIndex(k)
			if !old.IsValid() || !reflect.DeepEqual(old.Interface(), v.Interface()) {
				raw, _ := json.Marshal(v.Interface())
				out = append(out, Change{Field: field, Key: k.String(), Value: raw})
			}
		}
		for _, k := range x.MapKeys() {
			if !y.MapIndex(k).IsValid() {
				out = append(out, Change{Field: field, Key: k.String(), Delete: true})
			}
		}
	}
	return out
}
func apply(s *State, event Event) error {
	v := reflect.ValueOf(s).Elem()
	typ := v.Type()
	for _, c := range event.Changes {
		found := false
		for i := 0; i < v.NumField(); i++ {
			if typ.Field(i).Tag.Get("json") != c.Field {
				continue
			}
			found = true
			f := v.Field(i)
			if f.Kind() == reflect.Map {
				if f.IsNil() {
					f.Set(reflect.MakeMap(f.Type()))
				}
				k := reflect.ValueOf(c.Key)
				if c.Delete {
					f.SetMapIndex(k, reflect.Value{})
				} else {
					p := reflect.New(f.Type().Elem())
					if e := json.Unmarshal(c.Value, p.Interface()); e != nil {
						return e
					}
					f.SetMapIndex(k, p.Elem())
				}
			} else {
				if e := json.Unmarshal(c.Value, f.Addr().Interface()); e != nil {
					return e
				}
			}
			break
		}
		if !found {
			return fmt.Errorf("unknown event field %s", c.Field)
		}
	}
	s.Seq = event.Seq
	return nil
}
func (s *Store) load() (State, error) {
	var st State
	b, e := os.ReadFile(s.path("state.json"))
	if e == nil {
		if e = json.Unmarshal(b, &st); e != nil {
			return st, fmt.Errorf("snapshot: %w", e)
		}
		if st.Version != Version {
			return st, fmt.Errorf("instance schema %d, binary schema %d: no implicit migration; preserve old instance and seed v2 separately", st.Version, Version)
		}
	} else if !errors.Is(e, os.ErrNotExist) {
		return st, e
	}
	sv := reflect.ValueOf(&st).Elem()
	for i := 0; i < sv.NumField(); i++ {
		if sv.Field(i).Kind() == reflect.Map && sv.Field(i).IsNil() {
			sv.Field(i).Set(reflect.MakeMap(sv.Field(i).Type()))
		}
	}
	f, e := os.OpenFile(s.path("events.jsonl"), os.O_RDWR, 0600)
	if e != nil {
		return st, e
	}
	defer f.Close()
	r := bufio.NewReader(f)
	offset := int64(0)
	seq := int64(0)
	for {
		line, err := r.ReadBytes('\n')
		if err == io.EOF {
			if len(line) > 0 {
				if e = f.Truncate(offset); e != nil {
					return st, e
				}
				if e = f.Sync(); e != nil {
					return st, e
				}
			}
			break
		}
		if err != nil {
			return st, err
		}
		offset += int64(len(line))
		var env envelope
		if e = json.Unmarshal(line, &env); e != nil || Digest(env.Event) != env.Hash {
			return st, fmt.Errorf("corrupt event after seq %d", seq)
		}
		var ev Event
		if e = json.Unmarshal(env.Event, &ev); e != nil {
			return st, e
		}
		if ev.Seq != seq+1 {
			return st, fmt.Errorf("event sequence gap")
		}
		seq = ev.Seq
		if ev.Seq > st.Seq {
			if e = apply(&st, ev); e != nil {
				return st, e
			}
		}
	}
	if seq != st.Seq {
		return st, fmt.Errorf("snapshot ahead of event ledger")
	}
	return st, Validate(st)
}
func (s *Store) commit(before, after State, actor, kind, reason string) error {
	after.Seq = before.Seq + 1
	ev := Event{Seq: after.Seq, At: s.Now(), Actor: actor, Kind: kind, Reason: reason, Changes: diff(before, after)}
	b, e := json.Marshal(ev)
	if e != nil {
		return e
	}
	raw, _ := json.Marshal(envelope{Event: b, Hash: Digest(b)})
	f, e := os.OpenFile(s.path("events.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	_, e = f.Write(append(raw, '\n'))
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return e
	}
	b, _ = json.MarshalIndent(after, "", "  ")
	if e = Atomic(s.path("state.json"), b); e != nil {
		return fmt.Errorf("transaction %d committed; snapshot repair pending: %w", after.Seq, e)
	}
	projection := struct {
		Config Config          `json:"config"`
		Nodes  map[string]Node `json:"nodes"`
		Edges  map[string]Edge `json:"edges"`
		Items  map[string]Item `json:"items"`
	}{after.Config, after.Nodes, after.Edges, after.Items}
	b, _ = json.MarshalIndent(projection, "", "  ")
	return Atomic(filepath.Join(s.Dir, "brain.json"), b)
}
func (s *Store) History(id string, offset, limit int) ([]Event, error) {
	l, e := s.lock()
	if e != nil {
		return nil, e
	}
	defer unlock(l)
	f, e := os.Open(s.path("events.jsonl"))
	if e != nil {
		return nil, e
	}
	defer f.Close()
	r := bufio.NewReader(f)
	out := []Event{}
	seen := 0
	for {
		b, e := r.ReadBytes('\n')
		if e == io.EOF {
			break
		}
		if e != nil {
			return nil, e
		}
		var env envelope
		if e = json.Unmarshal(b, &env); e != nil {
			return nil, e
		}
		if Digest(env.Event) != env.Hash {
			return nil, fmt.Errorf("corrupt event")
		}
		var ev Event
		_ = json.Unmarshal(env.Event, &ev)
		match := id == ""
		for _, c := range ev.Changes {
			if c.Key == id {
				match = true
			}
		}
		if match {
			if seen >= offset && len(out) < limit {
				out = append(out, ev)
			}
			seen++
		}
		if len(out) >= limit {
			break
		}
	}
	return out, nil
}
func Decode(raw []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if e := d.Decode(v); e != nil {
		return e
	}
	if e := d.Decode(new(any)); e != io.EOF {
		return fmt.Errorf("expected exactly one JSON value")
	}
	return nil
}
