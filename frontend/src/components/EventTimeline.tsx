/** Chronological event trace, shared by the overview and the Safety and Logs page. */

import { useEffect, useRef } from 'react';

import type { EventEnvelope, EventType } from '@/api/types';
import { severityColor } from '@/lib/colors';
import { clockTime } from '@/lib/format';
import { EmptyState } from './primitives';

export function EventTimeline({
  events,
  limit = 60,
  types,
  autoScroll = true,
  emptyHint,
}: {
  events: EventEnvelope[];
  limit?: number;
  types?: EventType[];
  autoScroll?: boolean;
  emptyHint?: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const filtered = (types ? events.filter((e) => types.includes(e.type)) : events)
    .slice(-limit)
    .reverse();

  useEffect(() => {
    if (autoScroll && scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [filtered.length, autoScroll]);

  if (filtered.length === 0) {
    return (
      <EmptyState
        title="Nothing recorded yet."
        hint={emptyHint ?? 'Events appear as the controller observes and decides.'}
      />
    );
  }

  return (
    <div className="timeline" ref={scrollRef}>
      {filtered.map((event) => (
        <div className="timeline__row" key={event.event_id}>
          <span className="timeline__time">{clockTime(event.timestamp)}</span>
          <span
            className="timeline__dot"
            style={{ background: severityColor(event.severity) }}
            aria-hidden="true"
          />
          <span className="timeline__text">
            {event.summary || event.type}
            <span className="faint small"> · {event.type}</span>
          </span>
        </div>
      ))}
    </div>
  );
}
