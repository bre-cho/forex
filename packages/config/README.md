# config

Shared configuration for the Forex monorepo.

## Contents

- `eslint/` — Shared ESLint configurations
- `tailwind/` — Shared Tailwind CSS preset
- `tsconfig/` — Shared TypeScript configs (base, nextjs, react-library)
- `prettier/` — Shared Prettier config

## Usage

### ESLint

```json
{
  "extends": ["@forex/config/eslint/next"]
}
```

### TypeScript

```json
{
  "extends": "@forex/config/tsconfig/nextjs.json"
}
```

### Tailwind

```js
// tailwind.config.ts
import { sharedPreset } from "@forex/config/tailwind";
export default { presets: [sharedPreset] };
```
