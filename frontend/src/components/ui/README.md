# Shared UI components

These components were added from the official [shadcn/ui](https://ui.shadcn.com/)
registry with the `new-york` style. The project uses Tailwind CSS 3, so these are
the [Tailwind 3 compatible components](https://v3.shadcn.com/), backed by Radix UI.
Their source lives here and is maintained with the application; see
`LICENSE.shadcn` for upstream attribution.

Use these primitives in the workbench, reports, comparison and connection
settings instead of copying button, input, checkbox, picker or tab styles into
pages. Keep quantitative charts in ECharts and notifications in Sonner.

- Import components from `@/components/ui/<name>` and `cn` from `@/shared/cn`.
- Set theme tokens in `src/index.css` and `tailwind.config.ts`; both themes retain
  the workbench palette. `Badge` adds a semantic `success` variant.
- Give controls visible labels or accessible names. Pass `dir` to Radix Select
  and Tabs using the active language (`i18n.dir(i18n.language)`)
  so lazy locale loading cannot temporarily reverse keyboard navigation.
  Select item spacing uses logical RTL properties.
- Use `Button asChild` with router links. Use `disabled` for unavailable actions.
- Keep dates as native date inputs wrapped in `Input`.
- Radix Select reserves the empty string for placeholders. Use distinct sentinel
  values for "all" / "none" and prefix actual identifiers to avoid collisions.
- Tests should operate Radix controls through keyboard or realistic user events,
  not `fireEvent.change` on trigger buttons. `src/tests/select.ts` provides a
  keyboard helper; the test setup supplies jsdom's missing `scrollIntoView`.

To add a needed component, run `npx shadcn@latest add <component>` from `frontend/`.
Review the generated diff against `components.json` and the existing Tailwind 3
configuration. Do not overwrite local palette, RTL or accessibility adaptations
when updating components. Run `npm run test:run` and `npm run build` afterward.

TanStack Table is a future option for richer experiment tables; it is not a
dependency of this initial component integration.
