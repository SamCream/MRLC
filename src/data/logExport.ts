import type {AuditLog} from '../domain/models';
export type LocalLogExportFormat='jsonl'|'csv';
const columns=['id','timestamp','level','source','projectId','taskId','traceId','message'] as const;
function quoteCsv(value:unknown):string{
  const text=String(value??'');
  // Exported logs may be opened in Excel/Calc. Escape spreadsheet formula prefixes.
  const safe=/^[\s]*[=+@\-]/.test(text)?`'${text}`:text;
  return `"${safe.replace(/"/g,'""')}"`;
}
/** Local export only: precisely the loaded rows, never a claim of complete audit coverage. */
export function serializeLogs(rows:AuditLog[],format:LocalLogExportFormat):string{
  if(format==='jsonl')return rows.map(row=>JSON.stringify(row)).join('\n')+(rows.length?'\n':'');
  return columns.join(',')+'\r\n'+rows.map(row=>columns.map(column=>quoteCsv(row[column])).join(',')).join('\r\n')+(rows.length?'\r\n':'');
}
