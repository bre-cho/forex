export default function PricingPage() {
  const plans = [
    { name: 'Free', price: '$0', bots: 1, live: false, ai: false },
    { name: 'Starter', price: '$29/mo', bots: 3, live: true, ai: false },
    { name: 'Pro', price: '$79/mo', bots: 10, live: true, ai: true },
    { name: 'Enterprise', price: 'Custom', bots: 'Unlimited', live: true, ai: true },
  ];
  return (
    <div className="min-h-screen bg-surface py-20 px-4">
      <h1 className="text-4xl font-bold text-white text-center mb-12">Pricing</h1>
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 max-w-5xl mx-auto">
        {plans.map((p) => (
          <div key={p.name} className="bg-surface-muted rounded-xl p-6 text-white">
            <h2 className="text-xl font-bold text-brand">{p.name}</h2>
            <p className="text-3xl font-bold my-3">{p.price}</p>
            <ul className="text-gray-300 space-y-2 text-sm">
              <li>Up to {p.bots} bots</li>
              <li>{p.live ? '✅' : '❌'} Live trading</li>
              <li>{p.ai ? '✅' : '❌'} AI features</li>
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
