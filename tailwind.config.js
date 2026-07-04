/**
 * Tailwind config for the standalone Tailwind CLI (no Node/npm required).
 *
 * `content` lists every file the CLI scans for class names so it can tree-shake
 * the output CSS. Includes the Jinja templates and any JS that toggles classes.
 * The `safelist` keeps a few classes that are only ever produced via Alpine
 * `:class` bindings (dynamic strings the scanner can't see).
 */
module.exports = {
  content: [
    "./app/templates/**/*.html",
    "./app/static/js/**/*.js",
  ],
  safelist: [
    "bg-amber-600", "hover:bg-amber-500",
    "bg-emerald-600", "hover:bg-emerald-500",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
