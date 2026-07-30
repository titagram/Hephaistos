import { HADES_ASCII_ART } from '@/branding/identity'
import { cn } from '@/lib/utils'

export function HadesAsciiMark({ className, ...props }: React.ComponentProps<'pre'>) {
  return (
    <pre
      aria-hidden="true"
      className={cn('max-w-full overflow-hidden text-xs leading-none', className)}
      {...props}
    >
      {HADES_ASCII_ART}
    </pre>
  )
}
