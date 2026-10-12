import gymnasium as gym
from gymnasium import spaces
import numpy as np

class ToyTradingEnv(gym.Env):
    """
    極度簡單的 5 天測試環境 (Sanity Check)。
    股價連續 5 天上漲 (10, 12, 14, 16, 18)。
    如果 RL 模型正確，它應該學會在第 1 天直接 All in (買入並持有到最後)。
    """
    def __init__(self):
        super(ToyTradingEnv, self).__init__()
        
        # 價格序列 (5天，絕對必勝的連續上漲趨勢)
        self.prices = np.array([10.0, 12.0, 14.0, 16.0, 18.0], dtype=np.float32)
        self.max_steps = len(self.prices) - 1
        
        # 動作空間：0 = 持有現金 (空倉), 1 = 全倉買入 (All In)
        self.action_space = spaces.Discrete(2)
        
        # 狀態空間 (State Vector): 
        # [0]: 目前步數 (Normalized 0~1)
        # [1]: 目前股價 (Normalized，除以初始價格 10)
        # [2]: 目前持倉狀態 (0 或 1)
        # [3]: 目前未實現損益 PnL (如果有持倉)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(4,), dtype=np.float32
        )
        
        self.reset()
        
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        self.position = 0 # 0 = 空倉, 1 = 滿倉
        self.entry_price = 0.0
        self.balance = 1000.0 # 初始本金
        self.net_worth = 1000.0
        
        return self._next_observation(), {}
        
    def _next_observation(self):
        step_norm = self.current_step / self.max_steps
        price_norm = self.prices[self.current_step] / 10.0
        
        pnl = 0.0
        if self.position == 1:
            pnl = (self.prices[self.current_step] - self.entry_price) / self.entry_price
            
        obs = np.array([
            step_norm,
            price_norm,
            float(self.position),
            pnl
        ], dtype=np.float32)
        return obs
        
    def step(self, action):
        prev_net_worth = self.net_worth
        current_price = self.prices[self.current_step]
        
        # 執行動作
        if action == 1 and self.position == 0:
            # 買入 (All In)
            self.position = 1
            self.entry_price = current_price
            # 假設無手續費
        elif action == 0 and self.position == 1:
            # 賣出 (平倉)
            self.position = 0
            # 結算盈虧到本金
            roi = (current_price - self.entry_price) / self.entry_price
            self.balance = self.balance * (1 + roi)
            self.entry_price = 0.0
            
        # 推進時間
        self.current_step += 1
        
        # 計算新的淨值
        next_price = self.prices[self.current_step] if self.current_step <= self.max_steps else self.prices[-1]
        
        if self.position == 1:
            # 如果持有股票，淨值跟隨當前價格變化
            roi = (next_price - self.entry_price) / self.entry_price
            self.net_worth = self.balance * (1 + roi)
        else:
            self.net_worth = self.balance
            
        # Reward = 淨值的變化量 (Profit)
        reward = self.net_worth - prev_net_worth
        
        # 判斷是否結束
        done = self.current_step >= self.max_steps
        truncated = False
        
        obs = self._next_observation()
        info = {
            "net_worth": self.net_worth,
            "position": self.position
        }
        
        return obs, float(reward), done, truncated, info
