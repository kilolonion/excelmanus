export default function BackgroundOrbs() {
  return (
    <>
      {/* Primary green orb — top right */}
      <div className="pointer-events-none fixed -right-16 -top-16 z-0 h-[340px] w-[340px] animate-orb-float rounded-full bg-[radial-gradient(circle,rgba(33,115,70,.09)_0%,rgba(51,168,103,.04)_40%,transparent_70%)] blur-[90px]" />

      {/* Secondary green orb — bottom left */}
      <div className="pointer-events-none fixed -bottom-12 -left-8 z-0 h-[280px] w-[280px] animate-orb-float rounded-full bg-[radial-gradient(circle,rgba(33,115,70,.07)_0%,transparent_65%)] blur-[80px] [animation-delay:1s] [animation-direction:reverse] [animation-duration:11s]" />

      {/* Blue accent orb — center */}
      <div className="pointer-events-none fixed left-[45%] top-[35%] z-0 h-[200px] w-[200px] animate-orb-float rounded-full bg-[radial-gradient(circle,rgba(0,120,212,.045)_0%,transparent_65%)] blur-[80px] [animation-delay:2.5s] [animation-duration:13s]" />

      {/* Warm accent orb — top left */}
      <div className="pointer-events-none fixed -left-20 top-[15%] z-0 h-[160px] w-[160px] animate-orb-float rounded-full bg-[radial-gradient(circle,rgba(229,161,0,.035)_0%,transparent_65%)] blur-[70px] [animation-delay:3s] [animation-duration:14s]" />

      {/* Decorative ring — right */}
      <div className="pointer-events-none fixed right-[8%] top-[60%] z-0 h-[120px] w-[120px] animate-orb-float rounded-full border border-brand/[.04] [animation-delay:4s] [animation-duration:16s]" />

      {/* Tiny floating dot */}
      <div className="pointer-events-none fixed left-[20%] top-[25%] z-0 h-2 w-2 animate-orb-float rounded-full bg-brand/10 [animation-delay:1.5s] [animation-duration:9s]" />
      <div className="pointer-events-none fixed right-[25%] top-[70%] z-0 h-1.5 w-1.5 animate-orb-float rounded-full bg-brand-light/10 [animation-delay:3.5s] [animation-duration:10s]" />
    </>
  );
}
