import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * `daily_out_max` → `dailyOutMax`.
 *
 * Бекенд приймає snake_case, а віддає камелізоване (api/utils.dict_to_camel).
 * Через цю асиметрію поля лімітів читались за неіснуючими ключами й
 * показували нулі — і в картці, і в налаштуваннях банку. Конвертер один на
 * обидва місця: дві копії розійшлись би так само, як розходились мапи
 * банків.
 */
export const toCamel = <T extends string>(key: string): T =>
  key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()) as T;
