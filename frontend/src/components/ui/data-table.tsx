"use client";

import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useI18n } from "@/lib/i18n";
import { EmptyState, LoadingBlock } from "./layout";
import { Button } from "./primitives";

type Props<T> = {
  columns: ColumnDef<T, unknown>[];
  data: T[] | undefined;
  loading?: boolean;
  empty?: ReactNode;
  rowHref?: (row: T) => string;
  onRowClick?: (row: T) => void;
  /** server pagination */
  total?: number;
  page?: number;
  pageSize?: number;
  onPageChange?: (page: number) => void;
  /** client pagination (when no total given) */
  clientPageSize?: number;
  dense?: boolean;
  caption?: string;
  rowClassName?: (row: T) => string | undefined;
};

export function DataTable<T>({ columns, data, loading, empty, rowHref, onRowClick, total, page = 1, pageSize = 25,
  onPageChange, clientPageSize = 15, dense, caption, rowClassName }: Props<T>) {
  const { t } = useI18n();
  const router = useRouter();
  const [sorting, setSorting] = useState<SortingState>([]);
  const server = total !== undefined;
  // TanStack Table returns non-memoizable functions; this is expected (React Compiler skips this hook).
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: data ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: server ? undefined : getSortedRowModel(),
    getPaginationRowModel: server ? undefined : getPaginationRowModel(),
    manualSorting: server,
    initialState: { pagination: { pageSize: clientPageSize } },
  });
  const pages = server ? Math.max(1, Math.ceil((total ?? 0) / pageSize)) : table.getPageCount();
  const current = server ? page : table.getState().pagination.pageIndex + 1;
  const go = (p: number) => (server ? onPageChange?.(p) : table.setPageIndex(p - 1));

  if (loading && !data) return <LoadingBlock />;
  const rows = table.getRowModel().rows;
  return (
    <div className="w-full">
      <div className="overflow-x-auto scroll-thin">
        <table className="w-full text-sm border-collapse">
          {caption && <caption className="sr-only">{caption}</caption>}
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-line">
                {hg.headers.map((h) => {
                  const canSort = !server && h.column.getCanSort();
                  const sorted = h.column.getIsSorted();
                  return (
                    <th key={h.id} scope="col"
                      aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                      className={cn("text-left font-medium text-[12.5px] text-ink-3 px-3 py-2.5 whitespace-nowrap bg-surface",
                        (h.column.columnDef.meta as { align?: string } | undefined)?.align === "right" && "text-right")}>
                      {canSort ? (
                        <button className="inline-flex items-center gap-1 hover:text-ink" onClick={h.column.getToggleSortingHandler()}>
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {sorted === "asc" ? <ArrowUp className="size-3" /> : sorted === "desc" ? <ArrowDown className="size-3" /> : null}
                        </button>
                      ) : flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.map((r) => {
              const href = rowHref?.(r.original);
              const clickable = !!href || !!onRowClick;
              return (
                <tr key={r.id}
                  className={cn("border-b border-line last:border-b-0", clickable && "cursor-pointer hover:bg-surface-2 focus-within:bg-surface-2",
                    rowClassName?.(r.original))}
                  onClick={clickable ? () => (href ? router.push(href) : onRowClick?.(r.original)) : undefined}
                  onKeyDown={clickable ? (e) => { if (e.key === "Enter") (href ? router.push(href) : onRowClick?.(r.original)); } : undefined}
                  tabIndex={clickable ? 0 : undefined}>
                  {r.getVisibleCells().map((c) => (
                    <td key={c.id} className={cn("px-3 align-middle text-ink", dense ? "py-1.5" : "py-2.5",
                      (c.column.columnDef.meta as { align?: string } | undefined)?.align === "right" && "text-right num")}>
                      {flexRender(c.column.columnDef.cell, c.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && !loading && (empty ?? <EmptyState title={t("common.empty")} />)}
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between gap-3 px-3 py-2.5 border-t border-line text-[13px] text-ink-3">
          <span>{server ? t("common.results", { count: total ?? 0 }) : t("common.results", { count: data?.length ?? 0 })}</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" disabled={current <= 1} onClick={() => go(current - 1)} aria-label={t("common.previous")}>
              <ChevronLeft className="size-4" />
            </Button>
            <span className="num">{t("common.page", { page: current, pages })}</span>
            <Button size="sm" variant="ghost" disabled={current >= pages} onClick={() => go(current + 1)} aria-label={t("common.next")}>
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
