export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[minmax(0,32rem)_1fr]">
      <main className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">{children}</div>
      </main>
      <aside className="hidden bg-foreground p-12 text-background lg:flex lg:flex-col lg:justify-center" aria-hidden>
        <p className="mb-6 text-sm text-background/70">From messy manifest to a max bid</p>
        <div className="max-w-xl overflow-hidden rounded-lg border border-background/15">
          <table className="w-full text-left text-sm">
            <thead className="bg-background/10 text-background/70">
              <tr>
                <th className="px-4 py-2 font-medium">Line</th>
                <th className="px-4 py-2 font-medium">Qty</th>
                <th className="px-4 py-2 font-medium">Condition</th>
                <th className="px-4 py-2 font-medium">Net / unit</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-background/10 font-mono">
              <tr>
                <td className="px-4 py-3 font-sans">Cordless drill kit</td>
                <td className="px-4 py-3">48</td>
                <td className="px-4 py-3">Like new</td>
                <td className="px-4 py-3">$41.20</td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-sans">Air fryer 5 qt</td>
                <td className="px-4 py-3">120</td>
                <td className="px-4 py-3">Returns</td>
                <td className="px-4 py-3">$18.75</td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-sans">Wireless earbuds</td>
                <td className="px-4 py-3">244</td>
                <td className="px-4 py-3 text-amber-300">Salvage</td>
                <td className="px-4 py-3">$3.10</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-6 max-w-md text-sm text-background/70">
          Illustrative figures. Every line is priced after marketplace fees, freight and refurbishment, so the bid
          ceiling reflects what you actually keep.
        </p>
      </aside>
    </div>
  );
}
