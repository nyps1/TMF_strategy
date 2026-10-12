import os
from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env
from stable_baselines3.common.logger import configure

from env_toy import ToyTradingEnv

def main():
    print("Initializing Toy Environment...")
    env = ToyTradingEnv()
    
    # 檢查環境是否符合 Gymnasium 標準
    check_env(env, warn=True)
    print("Environment check passed!")
    
    # 設定 TensorBoard Log 路徑
    log_dir = "./tb_logs/"
    os.makedirs(log_dir, exist_ok=True)
    
    # 建立 PPO 模型
    print("Creating PPO Agent...")
    model = PPO(
        "MlpPolicy", 
        env, 
        verbose=1, 
        tensorboard_log=log_dir,
        learning_rate=0.003, # 較高的學習率讓它在簡單問題上快點收斂
        n_steps=16, # 縮短收集樣本長度
        batch_size=16,
        n_epochs=10
    )
    
    # 進行訓練 (500 steps 應該足夠收斂這種必勝局)
    print("Training started (500 timesteps)...")
    model.learn(total_timesteps=500, tb_log_name="PPO_Toy")
    print("Training finished.")
    
    # 測試訓練後的模型
    print("\n--- Testing trained agent ---")
    obs, info = env.reset()
    done = False
    truncated = False
    
    while not done and not truncated:
        action, _states = model.predict(obs, deterministic=True)
        obs, reward, done, truncated, info = env.step(action)
        
        # 轉換 step action 到易讀格式
        action_name = "Buy(All In)" if action == 1 else "Hold(Cash) / Sell"
        print(f"Step {env.current_step}: Action = {action_name}, Reward = {reward:.2f}, Net Worth = {info['net_worth']:.2f}, Position = {info['position']}")
        
    print(f"Final Net Worth: {info['net_worth']:.2f} (Expected > 1000 if it learned to buy)")
    
if __name__ == "__main__":
    main()
