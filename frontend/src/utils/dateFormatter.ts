export const APP_TIME_ZONE = 'America/New_York';

export function parseApiDate(dateString: string): Date {
  // API timestamps are stored as naive UTC values in the database. Treat a
  // missing offset as UTC before converting to the display timezone.
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(dateString);
  return new Date(hasTimezone ? dateString : `${dateString}Z`);
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
