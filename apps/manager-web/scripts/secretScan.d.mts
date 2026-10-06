export interface Rule { name: string; re: RegExp }
export const RULES: Rule[];
export function scanDir(dir: string, opts?: { skip?: (file: string) => boolean }): { file: string; rule: string }[];
export function scanText(text: string): string[];
