import { onMounted, ref } from 'vue';
import { get, isAxiosError } from '@/api/client';
import type { BundleHistoryItem } from '@/types/bundle';

export function useBundleHistory() {
  const items = ref<BundleHistoryItem[]>([]);
  const loading = ref(true);
  const error = ref<string | null>(null);

  const refresh = async () => {
    loading.value = true;
    error.value = null;
    try {
      items.value = await get<BundleHistoryItem[]>('/bundle-history?limit=1000');
    } catch (err) {
      if (isAxiosError(err)) {
        error.value = (err.response?.data as { detail?: string } | undefined)?.detail ?? err.message;
      } else {
        error.value = err instanceof Error ? err.message : 'Unable to load bundle history.';
      }
      items.value = [];
    } finally {
      loading.value = false;
    }
  };

  onMounted(refresh);

  return { items, loading, error, refresh };
}
