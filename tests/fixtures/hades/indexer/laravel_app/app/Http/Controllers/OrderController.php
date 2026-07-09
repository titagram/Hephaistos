<?php

namespace App\Http\Controllers;

use App\Services\OrderService;

class OrderController extends Controller
{
    public function show($id)
    {
        $order = Order::find($id);
        return $this->sendResponse($order);
    }

    public function store(Request $request, OrderService $orders)
    {
        $order = $orders->create($request->all());
        return $this->sendResponse($order);
    }

    private function sendResponse($data)
    {
        return response()->json($data);
    }
}
