export const APP_TIME_ZONE = 'America/New_York';

export type BundleEndingStatus = 'endingToday' | 'endingTomorrow' | null;

export function parseApiDate(dateString: string): Date {
  // API timestamps are stored as naive UTC values in the database. Treat a
  // missing offset as UTC before converting to the display timezone.
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(dateString);
  return new Date(hasTimezone ? dateString : `${dateString}Z`);
}

function easternCalendarDay(date: Date): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: APP_TIME_ZONE,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((item) => item.type === type)?.value);

  return Date.UTC(part('year'), part('month') - 1, part('day')) / 86_400_000;
}

/**
 * Return a localized-message key when a bundle's end date falls today or
 * tomorrow in Eastern time. API timestamps without an offset are interpreted
 * as UTC, matching formatDate.
 */
export function getBundleEndingStatus(
  endDateString: string | undefined | null,
  now: Date = new Date(),
): BundleEndingStatus {
  if (!endDateString) return null;

  const endDate = parseApiDate(endDateString);
  if (Number.isNaN(endDate.getTime()) || Number.isNaN(now.getTime())) return null;

  const dayDifference = easternCalendarDay(endDate) - easternCalendarDay(now);
  if (dayDifference === 0) return 'endingToday';
  if (dayDifference === 1) return 'endingTomorrow';
  return null;
}

export interface DateFormatOptions {
  includeTime?: boolean;
  includeSeconds?: boolean;
  format?: 'short' | 'long' | 'datetime';
  locale?: string;
}

/**
 * Formatea una fecha según el locale proporcionado o por defecto
 */
export function formatDate(
  dateString: string | undefined | null,
  options: DateFormatOptions = {}
): string {
  if (!dateString) return '—';

  const date = parseApiDate(dateString);
  const dateLocale = options.locale === 'es' ? 'es-ES' : 'en-US';

  const {
    includeTime = true,
    includeSeconds = false,
    format = 'datetime',
  } = options;

  if (format === 'short') {
    return date.toLocaleDateString(dateLocale, {
      month: 'short',
      day: 'numeric',
      timeZone: APP_TIME_ZONE,
    });
  }

  if (format === 'long') {
    return date.toLocaleDateString(dateLocale, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      timeZone: APP_TIME_ZONE,
    });
  }

  // datetime format (default)
  const formatOptions: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone: APP_TIME_ZONE,
  };

  if (includeTime) {
    formatOptions.hour = '2-digit';
    formatOptions.minute = '2-digit';
  }

  if (includeSeconds) {
    formatOptions.second = '2-digit';
  }

  return date.toLocaleString(dateLocale, formatOptions);
}
