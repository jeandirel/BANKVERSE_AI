import next from "eslint-config-next";

export default [
  ...next,
  { ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**"] },
  { rules: { "react-hooks/set-state-in-effect": "off" } },
];
