export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[minmax(0,32rem)_1fr]">
      <main className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">{children}</div>
      </main>
      <aside className="hidden bg-foreground p-12 text-background lg:flex lg:flex-col lg:justify-center" aria-hidden>
        <p className="mb-6 text-sm text-background/70">Supplier quotes, side by side</p>
        <div className="max-w-xl overflow-hidden rounded-lg border border-background/15">
          <table className="w-full text-left text-sm">
            <thead className="bg-background/10 text-background/70">
              <tr>
                <th className="px-4 py-2 font-medium">Supplier</th>
                <th className="px-4 py-2 font-medium">Unit price</th>
                <th className="px-4 py-2 font-medium">Lead time</th>
                <th className="px-4 py-2 font-medium">Incoterms</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-background/10 font-mono">
              <tr>
                <td className="px-4 py-3 font-sans">Northwind Metals</td>
                <td className="px-4 py-3">€4.20</td>
                <td className="px-4 py-3">21 days</td>
                <td className="px-4 py-3">FOB</td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-sans">Kessler GmbH</td>
                <td className="px-4 py-3">€4.55</td>
                <td className="px-4 py-3">14 days</td>
                <td className="px-4 py-3 text-amber-300">unknown</td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-sans">Orion Cast</td>
                <td className="px-4 py-3">€3.98</td>
                <td className="px-4 py-3 text-amber-300">unknown</td>
                <td className="px-4 py-3">EXW</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-6 max-w-md text-sm text-background/70">
          Anything a supplier did not state stays marked unknown. Nothing is assumed, so every comparison can be traced
          back to the quote it came from.
        </p>
      </aside>
    </div>
  );
}
