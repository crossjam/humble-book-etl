import { describe, expect, it } from 'vitest';
import { getBundleEndingStatus } from '@/utils/dateFormatter';

describe('getBundleEndingStatus', () => {
  const now = new Date('2026-11-10T17:00:00Z'); // noon in New York

  it('marks an end timestamp later today in Eastern time', () => {
    expect(getBundleEndingStatus('2026-11-11T04:30:00Z', now)).toBe('endingToday');
  });

  it('marks an end timestamp tomorrow in Eastern time', () => {
    expect(getBundleEndingStatus('2026-11-11T05:30:00Z', now)).toBe('endingTomorrow');
  });

  it('returns no label for dates outside today and tomorrow', () => {
    expect(getBundleEndingStatus('2026-11-12T05:00:00Z', now)).toBeNull();
    expect(getBundleEndingStatus('2026-11-10T04:59:00Z', now)).toBeNull();
  });

  it('handles a daylight-saving transition using Eastern calendar days', () => {
    const beforeSpringForward = new Date('2026-03-08T12:00:00Z');
    expect(
      getBundleEndingStatus('2026-03-09T04:30:00Z', beforeSpringForward),
    ).toBe('endingTomorrow');
  });

  it('returns no label for missing or invalid end dates', () => {
    expect(getBundleEndingStatus(null, now)).toBeNull();
    expect(getBundleEndingStatus('not-a-date', now)).toBeNull();
  });
});
