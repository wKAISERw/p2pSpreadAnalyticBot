import useSWR from 'swr';
import { api } from '../services/api';
import { BankProfile } from '../types';

/**
 * Довідник банків із GET /banks/profiles.
 *
 * До цього список банків жив копією просто в компоненті — шість штук, і
 * завести картку Ощадбанку чи Таскомбанку через сайт було неможливо, хоча
 * бот їх знає. Заразом копія нічого не знала про ліміти й комісії: поле
 * «місячний ліміт 60 000» виглядало магічним числом.
 *
 * Довідник статичний, тож перезапитувати його немає сенсу.
 */
export function useBankProfiles() {
  const { data, error, isLoading } = useSWR<BankProfile[]>(
    '/banks/profiles',
    () => api.getBankProfiles(),
    { revalidateOnFocus: false, revalidateIfStale: false, shouldRetryOnError: false }
  );

  const profiles = data ?? [];

  return {
    profiles,
    /** Тільки ті, що зустрічаються в стакані хоч однієї біржі. */
    tradable: profiles.filter((p) => p.tradable),
    bySlug: (slug: string) => profiles.find((p) => p.slug === slug),
    isLoading,
    error,
  };
}

/** Групи спільної ліцензії: слаг → решта банків тієї ж групи. */
export function licensePartners(profiles: BankProfile[], slug: string): BankProfile[] {
  const self = profiles.find((p) => p.slug === slug);
  if (!self?.licenseGroup) return [];
  return profiles.filter((p) => p.licenseGroup === self.licenseGroup && p.slug !== slug);
}
