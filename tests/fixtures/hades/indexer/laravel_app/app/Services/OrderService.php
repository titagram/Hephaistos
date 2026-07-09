<?php

namespace App\Services;

use App\Models\Order;

class OrderService
{
    public function create(array $attributes)
    {
        return Order::create($attributes);
    }
}
