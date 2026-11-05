import {$} from "./core.js";
import {documentDialog} from "./docdialog.js";

const REPORT_SECTIONS = [
  ["summary", "Сводка"],
  ["structure", "Структура"],
  ["coverage", "Покрытие"],
  ["plans", "Планы"],
  ["models", "Модели"],
  ["diagnostics", "Диагностика"],
];

function openReport() {
  return documentDialog({
    title: "Отчёт по проекту",
    endpoint: "/api/report",
    base: {},
    sections: REPORT_SECTIONS,
    json: true,
    jsonName: "report.json",
  });
}

export function initReport() {
  const button = $("reportBtn");
  if (button) button.onclick = openReport;
}
