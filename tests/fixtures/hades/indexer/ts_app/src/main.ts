import { add, multiply } from './helpers';

function calculateTotal(x: number, y: number): number {
  const sum = add(x, y);
  const product = multiply(x, y);
  return sum + product;
}

export default calculateTotal;
