import { useState } from 'react'

import { HADES_BRAND } from '@/branding/identity'
import { cn } from '@/lib/utils'

export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  const [assetFailed, setAssetFailed] = useState(false)

  return (
    <span
      aria-label={HADES_BRAND.productName}
      className={cn(
        'relative inline-flex size-14 shrink-0 items-center justify-center overflow-hidden rounded-md bg-[#11100d] text-[#cf8a2e]',
        className
      )}
      role="img"
      {...props}
    >
      <svg aria-hidden="true" className="size-[78%]" viewBox="0 0 64 64">
        <circle cx="32" cy="17" fill="none" r="11" stroke="currentColor" strokeWidth="4" />
        <path d="M32 28v25M20 41h24M21 54c7-4 15-4 22 0" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="4" />
      </svg>
      {!assetFailed && (
        <img
          alt=""
          aria-hidden="true"
          className="absolute inset-0 size-full"
          onError={() => setAssetFailed(true)}
          src={HADES_BRAND.markPath}
        />
      )}
    </span>
  )
}
