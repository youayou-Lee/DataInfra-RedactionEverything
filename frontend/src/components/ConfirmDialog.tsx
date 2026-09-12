// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useEffect } from 'react';
import { useT } from '@/i18n';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

interface Props {
  open: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmText,
  cancelText,
  danger = false,
  onConfirm,
  onCancel,
}: Props) {
  const t = useT();

  // Radix Dialog 的模态锁（body pointer-events:none / scroll-lock）依赖关闭动画
  // 时序恢复；确认回调若在同一 tick 触发大量 setState（如会话切换重建整棵预览），
  // 卸载清理会与渲染竞态而偶发残留——页面可见但所有点击失效，仅刷新可恢复。
  // 这里做确定性兜底：关闭后清掉残留锁样式；仅当 DOM 中已无任何打开的对话框
  // （含其他实例）时才清，避免误伤正当打开的模态。
  useEffect(() => {
    if (open) return;
    const timer = setTimeout(() => {
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return;
      const s = document.body.style;
      if (s.pointerEvents === 'none') s.pointerEvents = '';
      if (s.overflow === 'hidden') s.overflow = '';
      if (s.touchAction === 'none') s.touchAction = '';
    }, 300);
    return () => clearTimeout(timer);
  }, [open]);

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onCancel();
      }}
    >
      <DialogContent className="max-w-md p-6 [&>button]:hidden">
        <DialogHeader className="flex flex-col gap-2 text-left">
          <DialogTitle className="text-base font-semibold tracking-[-0.02em]">{title}</DialogTitle>
          <DialogDescription className="whitespace-pre-line text-sm text-muted-foreground">
            {message}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 pt-1 sm:justify-end">
          <Button type="button" onClick={onCancel} variant="outline">
            {cancelText ?? t('common.cancel')}
          </Button>
          <Button type="button" onClick={onConfirm} variant={danger ? 'destructive' : 'default'}>
            {confirmText ?? t('common.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
