<template>
  <section class="inactive-bundles-section">
    <BaseCard class="inactive-card">
      <div class="inactive-header">
        <div>
          <h2 class="section-title">{{ $t('inactiveBundles.title') }}</h2>
          <p class="section-description">{{ $t('inactiveBundles.description') }}</p>
        </div>
        <button class="refresh-button" type="button" :disabled="loading" @click="refresh">
          {{ loading ? $t('inactiveBundles.loading') : $t('inactiveBundles.refresh') }}
        </button>
      </div>

      <ErrorMessage :message="error ?? undefined" />
      <LoadingState v-show="loading" :message="$t('inactiveBundles.loading')" />

      <div v-if="!loading && !error" class="inactive-content">
        <p class="bundle-count">
          <strong>{{ items.length }}</strong>
          {{ $t('inactiveBundles.count') }}
        </p>

        <EmptyState
          v-if="items.length === 0"
          :message="$t('inactiveBundles.empty')"
        />
        <div v-else class="history-list">
          <article v-for="item in items" :key="item.id" class="history-entry">
            <div v-if="item.event" class="lifecycle-summary">
              <div>
                <strong>{{ item.bundle_title || item.machine_name }}</strong>
                <span class="event-badge">{{ eventLabel(item.event.event_type) }}</span>
              </div>
              <p>
                {{ $t('inactiveBundles.previousEnd') }} {{ formatDate(item.event.previous_end_at) }}
                → {{ $t('inactiveBundles.newEnd') }} {{ formatDate(item.event.new_end_at) }}
              </p>
            </div>

            <BundleListItem
              v-if="item.bundle"
              :bundle="item.bundle"
              :is-expanded="expandedBundle === item.id"
              :is-books-expanded="expandedBooks.has(item.id)"
              @toggle="toggleBundle(item.id)"
              @toggle-books="toggleBooks(item.id)"
              @view-json="viewBundleJson(item.bundle)"
              @download-json="downloadBundleJson(item.bundle)"
            />
            <div v-else class="event-only-item">
              <h3>{{ item.bundle_title || item.machine_name }}</h3>
              <p class="machine-name">{{ item.machine_name }}</p>
              <p>{{ $t('inactiveBundles.historicalOnly') }}</p>
              <dl>
                <div>
                  <dt>{{ $t('inactiveBundles.previousEnd') }}</dt>
                  <dd>{{ formatDate(item.event?.previous_end_at) }}</dd>
                </div>
                <div>
                  <dt>{{ $t('inactiveBundles.newEnd') }}</dt>
                  <dd>{{ formatDate(item.event?.new_end_at) }}</dd>
                </div>
              </dl>
            </div>
          </article>
        </div>
      </div>
    </BaseCard>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';
import BaseCard from '@/components/ui/BaseCard.vue';
import EmptyState from '@/components/ui/EmptyState.vue';
import ErrorMessage from '@/components/ui/ErrorMessage.vue';
import LoadingState from '@/components/ui/LoadingState.vue';
import BundleListItem from '@/components/utilities/bundles/BundleListItem.vue';
import { useBundleHistory } from '@/composables/useBundleHistory';
import type { Bundle, BundleLifecycleEvent } from '@/types/bundle';
import { downloadJson, viewJson } from '@/utils/jsonHandler';
import { formatDate as formatDateValue } from '@/utils/dateFormatter';

const { locale, t } = useI18n();
const { items, loading, error, refresh } = useBundleHistory();
const expandedBundle = ref<string | null>(null);
const expandedBooks = ref<Set<string>>(new Set());

function toggleBundle(itemId: string) {
  expandedBundle.value = expandedBundle.value === itemId ? null : itemId;
  expandedBooks.value.delete(itemId);
}

function toggleBooks(itemId: string) {
  if (expandedBooks.value.has(itemId)) {
    expandedBooks.value.delete(itemId);
  } else {
    expandedBooks.value.add(itemId);
  }
}

function viewBundleJson(bundle: Bundle) {
  viewJson(bundle);
}

function downloadBundleJson(bundle: Bundle) {
  downloadJson(bundle, `bundle-${bundle.machine_name || bundle.id}.json`);
}

function formatDate(value?: string | null) {
  return formatDateValue(value, { locale: locale.value });
}

function eventLabel(eventType: BundleLifecycleEvent['event_type']) {
  return t(`inactiveBundles.events.${eventType}`);
}
</script>

<style scoped lang="scss">
.inactive-bundles-section {
  padding: 32px;
  max-width: 1200px;
  margin: 0 auto;
}

.inactive-card {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.inactive-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.section-title {
  margin: 0 0 8px;
  color: var(--accent);
  font-size: 1.75rem;
}

.section-description {
  margin: 0;
  color: var(--text);
  opacity: 0.85;
}

.refresh-button {
  flex: 0 0 auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 16px;
  background: var(--primary);
  color: white;
  cursor: pointer;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.6;
  }
}

.bundle-count {
  margin: 0;
  color: var(--muted);
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.lifecycle-summary {
  padding: 12px 16px;
  border: 1px solid var(--border);
  border-bottom: 0;
  border-radius: 12px 12px 0 0;
  background: var(--surface);
  color: var(--text);
}

.lifecycle-summary > div {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.lifecycle-summary p,
.event-only-item p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 0.85rem;
}

.event-badge {
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--primary);
  color: white;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
}

.event-only-item {
  padding: 18px;
  border: 1px solid var(--border);
  border-left: 4px solid var(--text);
  border-radius: 12px;
  background: var(--surface);
  color: var(--text);
}

.event-only-item h3 {
  margin: 0;
  color: var(--accent);
}

.machine-name {
  font-family: Menlo, Monaco, "SFMono-Regular", Consolas, monospace;
  overflow-wrap: anywhere;
}

.event-only-item dl {
  display: grid;
  gap: 8px;
  margin: 16px 0 0;
}

.event-only-item dl div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}

.event-only-item dd {
  margin: 0;
  text-align: right;
}

@media (max-width: 768px) {
  .inactive-bundles-section {
    padding: 16px;
  }

  .inactive-header {
    flex-direction: column;
  }

  .event-only-item dl div {
    flex-direction: column;
    gap: 4px;
  }

  .event-only-item dd {
    text-align: left;
  }
}
</style>
