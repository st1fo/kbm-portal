import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
export function Dialog({title,children,onClose,wide}:{title:string;children:ReactNode;onClose:()=>void;wide?:boolean}){
 return <DialogPrimitive.Root open onOpenChange={open=>{if(!open)onClose()}}><DialogPrimitive.Portal><DialogPrimitive.Overlay className="dialog-overlay"/><DialogPrimitive.Content className={'dialog-content'+(wide?' dialog-wide':'')}><DialogPrimitive.Title>{title}</DialogPrimitive.Title><DialogPrimitive.Description className="sr-only">Vyplňte formulár a potvrďte zmeny.</DialogPrimitive.Description><DialogPrimitive.Close className="dialog-close" aria-label="Zavrieť"><X size={20}/></DialogPrimitive.Close>{children}</DialogPrimitive.Content></DialogPrimitive.Portal></DialogPrimitive.Root>
}
