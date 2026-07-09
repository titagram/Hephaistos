<?php

namespace App\Http\Controllers;

class OrderController extends Controller
{
    public function show($id)
    {
        $order = Order::find($id);
        return $this->sendResponse($order);
    }

    public function store(Request $request)
    {
        $order = Order::create($request->all());
        return $this->sendResponse($order);
    }

    private function sendResponse($data)
    {
        return response()->json($data);
    }
}
