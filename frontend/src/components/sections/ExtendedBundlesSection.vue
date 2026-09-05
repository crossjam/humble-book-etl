<template>
  <section class="extended-bundles-section">
    <BaseCard class="extended-card">
      <div class="extended-header">
        <div>
          <h2 class="section-title">{{ $t('extendedBundles.title') }}</h2>
          <p class="section-description">{{ $t('extendedBundles.description') }}</p>
        </div>
        <button class="refresh-button" type="button" :disabled="loading" @click="refresh">
          {{ loading ? $t('extendedBundles.loading') : $t('extendedBundles.refresh') }}
        </button>
      </div>

      <ErrorMessage :message="error ?? undefined" />
      <LoadingState v-show="loading" :message="$t('extendedBundles.loading')" />

      <div v-if="!loading && !error" class="extended-content">
        <p class="bundle-count">
          <strong>{{ events.length }}</strong>
          {{ $t('extendedBundles.count') }}
        </p>

        <EmptyState
          v-if="events.length === 0"
          :message="$t('extendedBundles.empty')"
        />
        <div v-else class="event-list">
          <article v-for="event in events" :key="event.id" class="event-item">
            <div class="event-title-row">
              <h3>{{ event.bundle_title || event.machine_name }}</h3>
              <span class="event-badge">{{ $t('extendedBundles.badge') }}</span>
            </div>
            <p class="machine-name">{{ event.machine_name }}</p>
            <dl class="event-details">
              <div>
                <dt>{{ $t('extendedBundles.previousEnd') }}</dt>
                <dd>{{ formatDate(event.previous_end_at) }}</dd>
              </div>
              <div>
                <dt>{{ $t('extendedBundles.newEnd') }}</dt>
                <dd>{{ formatDate(event.new_end_at) }}</dd>
              </div>
              <div>
                <dt>{{ $t('extendedBundles.observed') }}</dt>
                <dd>{{ formatDate(event.observed_at) }}</dd>
              </div>
            </dl>
          </article>
        </div>
      </div>
    </BaseCard>
  </section>
</template>

<script setup lang="ts">
import BaseCard from '@/components/ui/BaseCard.vue';
import EmptyState from '@/components/ui/EmptyState.vue';
import ErrorMessage from '@/components/ui/ErrorMessage.vue';
import LoadingState from '@/components/ui/LoadingState.vue';
import { useExtendedBundles } from '@/composables/useExtendedBundles';
import { useI18n } from 'vue-i18n';
import { formatDate as formatDateValue } from '@/utils/dateFormatter';

const { locale } = useI18n();
const { events, loading, error, refresh } = useExtendedBundles();

function formatDate(value?: string | null) {
  return formatDateValue(value, { locale: locale.value });
}
</script>

<style scoped lang="scss">
.extended-bundles-section {
  padding: 32px;
  max-width: 1200px;
  margin: 0 auto;
}

.extended-card {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.extended-header,
.event-title-row {
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

.section-description,
.machine-name,
.bundle-count {
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

.event-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}

.event-item {
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.event-title-row h3 {
  margin: 0;
  color: var(--accent);
  font-size: 1.05rem;
}

.event-badge {
  flex: 0 0 auto;
  padding: 4px 8px;
  border-radius: 999px;
  background: var(--primary);
  color: white;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
}

.machine-name {
  margin-top: 8px;
  font-family: Menlo, Monaco, "SFMono-Regular", Consolas, monospace;
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}

.event-details {
  display: grid;
  gap: 10px;
  margin: 18px 0 0;
}

.event-details div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}

.event-details dt {
  color: var(--muted);
}

.event-details dd {
  margin: 0;
  color: var(--text);
  text-align: right;
}

@media (max-width: 768px) {
  .extended-bundles-section {
    padding: 16px;
  }

  .extended-header,
  .event-title-row {
    flex-direction: column;
  }

  .event-details div {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .event-details dd {
    text-align: left;
  }
}
</style>
