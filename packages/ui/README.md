# ui

Shared React component library for the Forex platform.

Contains shadcn/ui-compatible components used across `apps/web` and `apps/admin`.

## Components

- `Button` — primary, secondary, destructive, ghost variants
- `Card` — card container with header/content/footer
- `Badge` — status badges
- `Input` — styled input field
- `Select` — dropdown select
- `Dialog` — modal dialog
- `Table` — data table
- `Tabs` — tabbed navigation
- `Tooltip` — hover tooltip
- `Spinner` — loading spinner

## Usage

```tsx
import { Button } from "@forex/ui/button";
import { Card } from "@forex/ui/card";
```

## Development

Components follow the shadcn/ui pattern:
- Radix UI primitives for accessibility
- Tailwind CSS for styling
- Class variance authority (cva) for variants
- TypeScript strict mode
