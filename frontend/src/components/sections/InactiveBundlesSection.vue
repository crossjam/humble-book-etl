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
          <strong>{{ inactiveBundles.length }}</strong>
          {{ $t('inactiveBundles.count') }}
        </p>

        <EmptyState
          v-if="inactiveBundles.length === 0"
          :message="$t('inactiveBundles.empty')"
        />
        <div v-else class="bundle-list">
          <BundleListItem
            v-for="bundle in inactiveBundles"
            :key="bundle.id"
            :bundle="bundle"
            :is-expanded="expandedBundle === bundle.id"
            :is-books-expanded="expandedBooks.has(bundle.id)"
            @toggle="toggleBundle(bundle.id)"
            @toggle-books="toggleBooks(bundle.id)"
            @view-json="viewBundleJson(bundle)"
            @download-json="downloadBundleJson(bundle)"
          />
        </div>
      </div>
    </BaseCard>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import BaseCard from '@/components/ui/BaseCard.vue';
import EmptyState from '@/components/ui/EmptyState.vue';
import ErrorMessage from '@/components/ui/ErrorMessage.vue';
import LoadingState from '@/components/ui/LoadingState.vue';
import BundleListItem from '@/components/utilities/bundles/BundleListItem.vue';
import { useBundles } from '@/composables/useBundles';
import type { Bundle } from '@/types/bundle';
import { downloadJson, viewJson } from '@/utils/jsonHandler';

const { bundles, loading, error, refresh } = useBundles({ includeInactive: true });
const expandedBundle = ref<string | null>(null);
const expandedBooks = ref<Set<string>>(new Set());

const inactiveBundles = computed(() =>
  bundles.value
    .filter((bundle) => !bundle.is_active || Boolean(bundle.archived_at))
    .sort((a, b) => {
      const dateA = a.verification_date ? new Date(a.verification_date).getTime() : 0;
      const dateB = b.verification_date ? new Date(b.verification_date).getTime() : 0;
      return dateB - dateA;
    }),
);

function toggleBundle(bundleId: string) {
  expandedBundle.value = expandedBundle.value === bundleId ? null : bundleId;
  expandedBooks.value.delete(bundleId);
}

function toggleBooks(bundleId: string) {
  if (expandedBooks.value.has(bundleId)) {
    expandedBooks.value.delete(bundleId);
  } else {
    expandedBooks.value.add(bundleId);
  }
}

function viewBundleJson(bundle: Bundle) {
  viewJson(bundle);
}

function downloadBundleJson(bundle: Bundle) {
  downloadJson(bundle, `bundle-${bundle.machine_name || bundle.id}.json`);
}
</script>

<style scoped lang="scss">
@use "@style/colors.scss" as *;

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

.bundle-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

@media (max-width: 768px) {
  .inactive-bundles-section {
    padding: 16px;
  }

  .inactive-header {
    flex-direction: column;
  }
}
</style>
